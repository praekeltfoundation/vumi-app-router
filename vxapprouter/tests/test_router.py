from vumi.tests.helpers import VumiTestCase
from vumi.dispatchers.tests.helpers import DispatcherHelper

from twisted.internet.defer import inlineCallbacks

from vxapprouter.router import ApplicationDispatcher


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

    def setUp(self):
        self.disp_helper = self.add_helper(
            DispatcherHelper(ApplicationDispatcher))

    def get_dispatcher(self, **config_extras):
        config = self.ROUTER_CONFIG.copy()
        config.update(config_extras)
        return self.disp_helper.get_dispatcher(config)

    @inlineCallbacks
    def test_something(self):
        d = yield self.get_dispatcher()
        print d
        assert True
