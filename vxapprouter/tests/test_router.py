from vumi.components.session import SessionManager
from vumi.dispatchers.tests.helpers import DispatcherHelper
from vumi.tests.helpers import VumiTestCase, PersistenceHelper

from twisted.internet.defer import inlineCallbacks, succeed

from vxapprouter.router import ApplicationDispatcher


class DummyError(Exception):
    """Custom exception to use in test cases."""


class TestApplicationRouter(VumiTestCase):

    ROUTER_CONFIG = {
        'invalid_input_message': 'Bad choice.\n1) Try Again',
        'error_message': 'Oops! Sorry!',
        'entries': [
            {
                'label': 'Flappy Bird',
                'endpoint': 'flappy-bird',
            },
        ],
        "receive_inbound_connectors": ["transport"],
        "receive_outbound_connectors": ["app1", "app2"],
    }

    @inlineCallbacks
    def setUp(self):
        self.persistence_helper = self.add_helper(PersistenceHelper())
        self.redis = yield self.persistence_helper.get_redis_manager()
        self.session_manager = SessionManager(self.redis)
        self.patch(
            ApplicationDispatcher, 'session_manager',
            lambda *a: succeed(self.session_manager))
        self.disp_helper = self.add_helper(
            DispatcherHelper(ApplicationDispatcher))

    def ch(self, connector_name):
        return self.disp_helper.get_connector_helper(connector_name)

    def assert_rkeys_used(self, *rkeys):
        broker = self.disp_helper.worker_helper.broker
        self.assertEqual(set(rkeys), set(broker.dispatched['vumi'].keys()))

    def assert_dispatched_endpoint(self, msg, endpoint, dispatched_msgs):
        msg.set_routing_endpoint(endpoint)
        self.assertEqual([msg], dispatched_msgs)

    def get_dispatcher(self, **config_extras):
        config = self.ROUTER_CONFIG.copy()
        config.update(config_extras)
        return self.disp_helper.get_dispatcher(config)

    @inlineCallbacks
    def test_first_inbound_message_routing(self):
        yield self.get_dispatcher()
        msg = yield self.ch("transport").make_dispatch_inbound("inbound")
        self.assert_rkeys_used('transport.inbound', 'transport.outbound')
        [reply] = self.ch('transport').get_dispatched_outbound()
        self.assertEqual(
            reply['content'],
            '\n'.join([
                "Please select a choice.",
                "1) Flappy Bird",
            ]))

        session = yield self.session_manager.load_session(msg['from_addr'])
        self.assertEqual(session['state'], ApplicationDispatcher.STATE_SELECT)
