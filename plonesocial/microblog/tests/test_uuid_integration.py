import unittest2 as unittest
from zope.interface import implements

from plone.uuid.interfaces import IUUID

from plone.app.testing import TEST_USER_ID, setRoles

from plonesocial.microblog.testing import \
    PLONESOCIAL_MICROBLOG_INTEGRATION_TESTING

from plonesocial.microblog.statuscontainer import BaseStatusContainer
from plonesocial.microblog.interfaces import IStatusUpdate
from plonesocial.microblog import statusupdate


class StatusContainer(BaseStatusContainer):
    """we don't care about permission checks for the uuid integration"""

    def _check_permission(self, perm="read"):
        return True


class StatusUpdate(statusupdate.StatusUpdate):
    """Override actual implementation with test features.
    Does NOT override the uuid functionality.
    """

    implements(IStatusUpdate)

    def __init__(self, text, context=None, userid='dude', creator=None):
        statusupdate.StatusUpdate.__init__(self, text, context)
        self.userid = userid
        if creator:
            self.creator = creator
        else:
            self.creator = userid

    def _init_userid(self):
        pass

    def _init_creator(self):
        pass


class TestUUIDIntegration(unittest.TestCase):
    """Verify plone.app.uuid integration for BaseStatusContainer
    and StatusUpdate"""

    layer = PLONESOCIAL_MICROBLOG_INTEGRATION_TESTING

    def setUp(self):
        self.portal = self.layer['portal']
        setRoles(self.portal, TEST_USER_ID, ['Manager'])

    def test_statuscontainer_context2uuid(self):
        """Unittests fake uuids. Integration test with real uuids."""
        self.portal.invokeFactory('Folder', 'f1', title=u"Folder 1")
        f1 = self.portal['f1']
        container = StatusContainer()
        self.assertEquals(container._context2uuid(f1), IUUID(f1))

    def test_statusupdate_context2uuid(self):
        """Unittests fake uuids. Integration test with real uuids."""
        self.portal.invokeFactory('Folder', 'f1', title=u"Folder 1")
        f1 = self.portal['f1']
        update = StatusUpdate('test')
        self.assertEquals(update._context2uuid(f1), IUUID(f1))

    def test_items_values_tag_context(self):
        """Unittests fake uuids. Integration test with real uuids."""
        container = StatusContainer()
        self.portal.invokeFactory('Folder', 'f1', title=u"Folder 1")
        mockcontext1 = self.portal['f1']
        self.portal.invokeFactory('Folder', 'f2', title=u"Folder 2")
        mockcontext2 = self.portal['f2']
        su1 = StatusUpdate('test #foo',
                           context=mockcontext1, userid='arnold')
        su2 = StatusUpdate('test #foo',
                           context=mockcontext2, userid='arnold')
        su3 = StatusUpdate('test #foo #bar',
                           context=mockcontext2, userid='arnold')
        su4 = StatusUpdate('test #foo #bar',
                           context=mockcontext2, userid='bernard')
        container.add(su1)
        container.add(su2)
        container.add(su3)
        container.add(su4)
        values = [x[1] for x in container.user_items(['arnold'],
                                                     tag='foo',
                                                     context=mockcontext1)]
        self.assertEqual([su1], values)
        values = [x[1] for x in container.user_items(['arnold'],
                                                     tag='bar',
                                                     context=mockcontext1)]
        self.assertEqual([], values)
        values = [x[1] for x in container.user_items(['bernard'],
                                                     tag='bar',
                                                     context=mockcontext1)]
        self.assertEqual([], values)
        values = [x[1] for x in container.user_items(['bernard'],
                                                     tag='bar',
                                                     context=mockcontext2)]
        self.assertEqual([su4], values)
        values = [x[1] for x in container.user_items(['arnold'],
                                                     tag='foo',
                                                     context=mockcontext2)]
        self.assertEqual([su3, su2], values)
