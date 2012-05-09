import threading
import Queue
import logging
import time
import math

from BTrees import LOBTree
from BTrees import OOBTree
from BTrees import LLBTree

from persistent import Persistent
import transaction
from Acquisition import aq_base
from Acquisition import Explicit

from zope.annotation.interfaces import IAnnotations
from zope.app.container.contained import ObjectAddedEvent
from zope.event import notify
from zope.interface import implements

from interfaces import IStatusContainer
from interfaces import IStatusUpdate

logger = logging.getLogger('plonesocial.microblog')

ANNOTATION_KEY = 'plonesocial.microblog:statuscontainer'

LOCK = threading.RLock()
STATUSQUEUE = Queue.PriorityQueue()

# max in-memory time in millisec before disk sync
MAX_QUEUE_AGE = 1000


class StatusContainer(Persistent, Explicit):

    implements(IStatusContainer)

    """This implements all of the storage logic.

    StatusUpdates are stored in the private __status_mapping BTree.
    A subset of BTree accessors are exposed, see interfaces.py.
    StatusUpdates are keyed by longint microsecond ids.

    Additionally, StatusUpdates are indexed by users (and TODO: tags).
    These indexes use the same longint microsecond IStatusUpdate.id.

    Special user_* prefixed accessors take an extra argument 'users',
    an interable of userids, and return IStatusUpdate keys, instances or items
    filtered by userids, in addition to the normal min/max statusid filters.

    Batching
    --------

    For performance reasons, an in-memory STATUSQUEUE is used.
    StatusContainer.add() puts StatusUpdates into the queue.

    .add() calls .autoflush(), which flushes the queue when
    .mtime is longer than MAX_QUEUE_AGE ago.

    So each .add() checks the queue. In a low-traffic site this will
    result in immediate disk writes (msg frequency < timeout).
    In a high-traffic site this will result on one write per timeout.

    Additionally, a non-interactive queue flush is set up via
    _schedule_flush() which uses a volatile _v_timer to set
    up a non-interactive queue flush. This ensures that the "last
    Tweet of the day" also gets committed to disk.

    To disable batch queuing, set MAX_QUEUE_AGE = 0
    """

    def __init__(self, context):
        self._mtime = 0
        self.context = context
        # primary storage: (long statusid) -> (object IStatusUpdate)
        self._status_mapping = LOBTree.LOBTree()
        # index by user: (string userid) -> (object TreeSet(long statusid))
        self._user_mapping = OOBTree.OOBTree()
        # index by tag: (string tag) -> (object TreeSet(long statusid))
        self._tag_mapping = OOBTree.OOBTree()

    def add(self, status):
        self._check_status(status)
        if MAX_QUEUE_AGE > 0:
            self.queue(status)
            # fallback sync in case of NO traffic (kernel timer)
            self._schedule_flush()
            # immediate sync on low traffic (old ._mtime)
            # postpones sync on high traffic (next .add())
            return self.autoflush()
        else:
            self.store(status)
            return 1  # immediate write

    def queue(self, status):
        STATUSQUEUE.put((status.id, status))

    def _schedule_flush(self):
        """A fallback queue flusher that runs without user interactions"""
        if not MAX_QUEUE_AGE > 0:
            return

        try:
            # non-persisted, absent on first request
            self._v_timer
        except AttributeError:
            # initialize on first request
            self._v_timer = None

        if self._v_timer is not None:
            # timer already running
            return

        # only a one-second granularity, round upwards
        timeout = int(math.ceil(float(MAX_QUEUE_AGE) / 1000))
        with LOCK:
            #logger.info("Setting timer")
            self._v_timer = threading.Timer(timeout,
                                            self._scheduled_autoflush)
            self._v_timer.start()

    def _scheduled_autoflush(self):
        """This method is run from the timer, outside a normal request scope.
        This requires an explicit commit on db write"""
        if self.autoflush():  # returns 1 on actual write
            transaction.commit()

    def autoflush(self):
        #logger.info("autoflush")
        if int(time.time() * 1000) - self._mtime > MAX_QUEUE_AGE:
            return self.flush_queue()  # 1 on write, 0 on noop
        return 0  # no write

    def flush_queue(self):
        #logger.info("flush_queue")

        with LOCK:
            # block autoflush
            self._mtime = int(time.time() * 1000)
            # cancel scheduled flush
            if self._v_timer is not None:
                #logger.info("Cancelling timer")
                self._v_timer.cancel()
                self._v_timer = None

        if STATUSQUEUE.empty():
            return 0  # no write

        while True:
            try:
                (id, status) = STATUSQUEUE.get(block=False)
                self.store(status)
            except Queue.Empty:
                break
        return 1  # confirmed write

    def store(self, status):
        # see ZODB/Btree/Interfaces.py
        # If the key was already in the collection, there is no change
        while not self._status_mapping.insert(status.id, status):
            status.id += 1
        self._idx_user(status)
        self._idx_tag(status)
        self._notify(status)

    def _check_status(self, status):
        if not IStatusUpdate.providedBy(status):
            raise ValueError("IStatusUpdate interface not provided.")

    def _notify(self, status):
        event = ObjectAddedEvent(status,
                                 newParent=self.context, newName=status.id)
        notify(event)
#        logger.info("Added StatusUpdate %s (%s: %s)",
#                    status.id, status.userid, status.text)

    def _idx_user(self, status):
        userid = unicode(status.userid)
        # If the key was already in the collection, there is no change
        # create user treeset if not already present
        self._user_mapping.insert(userid, LLBTree.LLTreeSet())
        # add status id to user treeset
        self._user_mapping[userid].insert(status.id)

    def _idx_tag(self, status):
        for tag in [unicode(tag) for tag in status.tags]:
            # If the key was already in the collection, there is no change
            # create tag treeset if not already present
            self._tag_mapping.insert(tag, LLBTree.LLTreeSet())
            # add status id to tag treeset
            self._tag_mapping[tag].insert(status.id)

    def clear(self):
        self._user_mapping.clear()
        self._tag_mapping.clear()
        return self._status_mapping.clear()

    ## blocked IBTree methods to protect index consistency
    ## (also not sensible for our use case)

    def insert(self, key, value):
        raise NotImplementedError("Can't allow that to happen.")

    def pop(self, k, d=None):
        raise NotImplementedError("Can't allow that to happen.")

    def setdefault(self, k, d):
        raise NotImplementedError("Can't allow that to happen.")

    def update(self, collection):
        raise NotImplementedError("Can't allow that to happen.")

    ## primary accessors

    def get(self, key):
        return self._status_mapping.get(key)

    def items(self, min=None, max=None):
        return self._status_mapping.items(min=min, max=max)

    def keys(self, min=None, max=None):
        return self._status_mapping.keys(min=min, max=max)

    def values(self, min=None, max=None):
        return self._status_mapping.values(min=min, max=max)

    def iteritems(self, min=None, max=None):
        return self._status_mapping.iteritems(min=min, max=max)

    def iterkeys(self, min=None, max=None):
        return self._status_mapping.iterkeys(min=min, max=max)

    def itervalues(self, min=None, max=None):
        return self._status_mapping.itervalues(min=min, max=max)

    ## user accessors

    # no user_get

    def user_items(self, users, min=None, max=None):
        return ((key, self.get(key)) for key
                in self.user_keys(users, min, max))

    def user_keys(self, users, min=None, max=None):
        if not users:
            return ()
        if type(users) == type('string'):
            users = [users]

        # collection of user LLTreeSet
        treesets = (self._user_mapping.get(userid) for userid in users
                    if userid in self._user_mapping.keys())
        merged = reduce(LLBTree.union, treesets, LLBTree.TreeSet())
        # list of longints is cheapest place to slice and sort
        keys = [x for x in merged.keys()
                if (not min or min <= x)
                and (not max or max >= x)]
        keys.sort()
        return keys

    def user_values(self, users, min=None, max=None):
        return (self.get(key) for key
                in self.user_keys(users, min, max))

    user_iteritems = user_items
    user_iterkeys = user_keys
    user_itervalues = user_values


def statusContainerAdapterFactory(context):
    """
    Adapter factory to store and fetch the status container from annotations.
    """
    annotions = IAnnotations(context)
    if not ANNOTATION_KEY in annotions:
        statuscontainer = StatusContainer(context)
        statuscontainer.__parent__ = aq_base(context)
        annotions[ANNOTATION_KEY] = aq_base(statuscontainer)
    else:
        statuscontainer = annotions[ANNOTATION_KEY]
    return statuscontainer.__of__(context)
