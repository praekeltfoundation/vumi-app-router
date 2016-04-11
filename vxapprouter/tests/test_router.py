import json
import copy

from vumi.components.session import SessionManager
from vumi.dispatchers.tests.helpers import DispatcherHelper
from vumi.tests.helpers import VumiTestCase, PersistenceHelper

from twisted.internet.defer import (
    inlineCallbacks, succeed, Deferred, returnValue)

from vxapprouter.router import ApplicationDispatcher


class DummyError(Exception):
    """Custom exception to use in test cases."""


def raise_error(*args, **kw):
    raise RuntimeError("An anomaly has been detected")


class TestApplicationRouter(VumiTestCase):

    DISPATCHER_CONFIG = {
        'invalid_input_message': 'Bad choice.',
        'error_message': 'Oops! Sorry!',
        'entries': [
            {
                'label': 'Flappy Bird',
                'endpoint': 'flappy-bird',
            },
        ],
        "routing_table": {
            "transport": {
                "flappy-bird": ["app1", "default"],
                "default": ["transport", "default"],
            },
            "app1": {
                "default": ["transport", "default"],
            },
        },
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

    @inlineCallbacks
    def assert_session(self, user_id, expected_session):
        session = yield self.session_manager.load_session(user_id)
        if 'created_at' in session:
            del session['created_at']
        self.assertEqual(session, expected_session)

    def setup_session(self, user_id, data):
        return self.session_manager.save_session(user_id, data)

    def get_dispatcher(self, **config_extras):
        config = self.DISPATCHER_CONFIG.copy()
        config.update(config_extras)
        return self.disp_helper.get_dispatcher(config)

    @inlineCallbacks
    def test_new_session_display_menu(self):
        yield self.get_dispatcher()
        msg = yield self.ch("transport").make_dispatch_inbound(
            "inbound", transport_name='transport')
        self.assert_rkeys_used('transport.inbound', 'transport.outbound')
        [reply] = self.ch('transport').get_dispatched_outbound()
        self.assertEqual(
            reply['content'],
            '\n'.join([
                "Please select a choice.",
                "1) Flappy Bird",
            ]))

        yield self.assert_session(msg['from_addr'], {
            'state': ApplicationDispatcher.STATE_SELECT,
            'endpoints': json.dumps(['flappy-bird']),
        })

    @inlineCallbacks
    def test_select_application_endpoint(self):
        """
        Retrieve endpoint choice from user and set currently active
        endpoint.
        """
        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECT,
            'endpoints': '["flappy-bird"]',
        })
        yield self.get_dispatcher()
        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            "1", session_event='resume', from_addr='123')

        # assert that message is forwarded to application
        [msg] = yield self.ch("app1").get_dispatched_inbound()
        self.assertEqual(msg['content'], None)
        self.assertEqual(msg['session_event'], 'new')

        # application sends reply
        yield self.ch("app1").make_dispatch_reply(msg, 'Flappy Flappy!')

        # assert that the user received a response
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'], 'Flappy Flappy!')

        yield self.assert_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })

    @inlineCallbacks
    def test_session_with_selected_endpoint(self):
        """
        Tests an ongoing USSD session with a previously selected endpoint
        """
        yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            'Up!', from_addr='123', session_event='resume',
            transport_name='transport')

        # assert that message is forwarded to application
        [msg] = self.ch("app1").get_dispatched_inbound()
        self.assertEqual(msg['content'], 'Up!')
        self.assertEqual(msg['session_event'], 'resume')

        # application sends reply
        yield self.ch("app1").make_dispatch_reply(
            msg, 'Game Over!\n1) Try Again!')

        # assert that the user received a response
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'],
                         'Game Over!\n1) Try Again!')

        yield self.assert_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })

    @inlineCallbacks
    def test_bad_input_for_endpoint_choice(self):
        """
        User entered bad input for the endpoint selection menu.
        """
        yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECT,
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            'foo', from_addr='123', session_event='resume',
            transport_name='transport')

        # assert that the user received a response
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'],
                         'Bad choice.\n\n1. Try Again')

        yield self.assert_session('123', {
            'state': ApplicationDispatcher.STATE_BAD_INPUT,
            'endpoints': '["flappy-bird"]',
        })

    @inlineCallbacks
    def test_state_bad_input_for_bad_input_prompt(self):
        """
        User entered bad input for the prompt telling the user
        that they entered bad input (ha! recursive).
        """
        yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_BAD_INPUT,
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            'foo', from_addr='123', session_event='resume',
            transport_name='transport')

        # assert that the user received a response
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'],
                         'Bad choice.\n\n1. Try Again')

        yield self.assert_session('123', {
            'state': ApplicationDispatcher.STATE_BAD_INPUT,
            'endpoints': '["flappy-bird"]',
        })

    @inlineCallbacks
    def test_state_good_input_for_bad_input_prompt(self):
        """
        User entered good input for the prompt telling the user
        that they entered bad input.
        """
        yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_BAD_INPUT,
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            '1', from_addr='123', session_event='resume',
            transport_name='transport')

        # assert that the user received a response
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'],
                         'Please select a choice.\n1) Flappy Bird')

        yield self.assert_session('123', {
            'state': ApplicationDispatcher.STATE_SELECT,
            'endpoints': '["flappy-bird"]',
        })

    @inlineCallbacks
    def test_runtime_exception_in_selected_handler(self):
        """
        Verifies that the worker handles an arbitrary runtime error gracefully,
        and sends an appropriate error message back to the user
        """
        dispatcher = yield self.get_dispatcher()

        # Make worker.target_endpoints raise an exception
        self.patch(dispatcher, 'target_endpoints', raise_error)

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            'Up!', from_addr='123', session_event='resume',
            transport_name='transport')

        # assert that the user received a response
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'],
                         'Oops! Sorry!')

        yield self.assert_session('123', {})

        errors = self.flushLoggedErrors(RuntimeError)
        self.assertEqual(len(errors), 1)

    @inlineCallbacks
    def test_session_invalidation_in_state_handler(self):
        """
        Verify that the router gracefully handles a configuration
        update while there is an active user session.

        A session is aborted if there is no longer an attached endpoint
        to which it refers.
        """
        config = copy.deepcopy(self.DISPATCHER_CONFIG)
        config['entries'][0]['endpoint'] = 'mama'
        yield self.get_dispatcher(**config)
        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            'Up!', from_addr='123', session_event='resume',
            transport_name='transport')
        # assert that the user received a response
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'],
                         'Oops! Sorry!')
        yield self.assert_session('123', {})

    @inlineCallbacks
    def test_state_selected_receive_close_inbound(self):
        """
        User sends 'close' msg to the active endpoint via the router.
        Verify that the message is forwarded and that the session for
        the user is cleared.
        """
        yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            None, from_addr='123', session_event='close',
            transport_name='transport')

        # assert app received forwarded 'close' message
        [msg] = yield self.ch("app1").get_dispatched_inbound()
        self.assertEqual(msg['content'], None)
        self.assertEqual(msg['session_event'], 'close')

        # assert that no response sent to user
        msgs = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msgs, [])

        # assert that session cleared
        yield self.assert_session('123', {})

    @inlineCallbacks
    def test_receive_close_inbound(self):
        """
        Same as the above test, but only for the case when
        an active endpoint has not yet been selected.
        """
        yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECT,
            'endpoints': '["flappy-bird"]'
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            None, from_addr='123', session_event='close',
            transport_name='transport')

        # assert that no app received a forwarded 'close' message
        self.assertEqual([], self.ch("app1").get_dispatched_inbound())
        self.assertEqual([], self.ch("app2").get_dispatched_inbound())
        self.assertEqual(msg['session_event'], 'close')

        # assert that no response sent to user
        self.assertEqual([], self.ch("transport").get_dispatched_outbound())

        # assert that session cleared
        yield self.assert_session('123', {})

    @inlineCallbacks
    def test_receive_close_outbound(self):
        """
        Application sends a 'close' message to the user via
        the router. Verify that the message is forwarded correctly,
        and that the session is terminated.
        """
        yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })

        # msg sent from user
        msg = yield self.ch("transport").make_dispatch_inbound(
            "3", from_addr='123', session_event='resume',
            transport_name='transport')

        # application quits session
        yield self.ch("app1").make_dispatch_reply(
            msg, 'Game Over!', session_event='close')

        # assert that user receives the forwarded 'close' message
        [msg] = self.ch("transport").get_dispatched_outbound()
        self.assertEqual(msg['content'], 'Game Over!')
        self.assertEqual(msg['session_event'], 'close')

        # assert that session cleared
        yield self.assert_session('123', {})

    @inlineCallbacks
    def test_get_menu_choice(self):
        """
        Verify that we parse user input correctly for menu prompts.
        """
        dispatcher = yield self.get_dispatcher()

        # good
        msg = self.disp_helper.make_inbound(content='3 ')
        choice = dispatcher.get_menu_choice(msg, (1, 4))
        self.assertEqual(choice, 3)

        # bad - out of range
        choice = dispatcher.get_menu_choice(msg, (1, 2))
        self.assertEqual(choice, None)

        # bad - non-numeric input
        msg = self.disp_helper.make_inbound(content='Foo ')
        choice = dispatcher.get_menu_choice(msg, (1, 2))
        self.assertEqual(choice, None)

    @inlineCallbacks
    def test_create_menu(self):
        """
        Create a menu prompt to choose between linked endpoints
        """
        dispatcher = yield self.get_dispatcher(
            entries=[{
                'label': 'Flappy Bird',
                'endpoint': 'flappy-bird',
            }, {
                'label': 'Mama',
                'endpoint': 'mama',
            }])
        text = dispatcher.create_menu((yield dispatcher.get_config({})))
        self.assertEqual(
            text, 'Please select a choice.\n1) Flappy Bird\n2) Mama')

    @inlineCallbacks
    def test_new_session_stores_valid_session_data(self):
        """
        Starting a new session sets all relevant session fields.
        """
        dispatcher = yield self.get_dispatcher()

        orig_handler = dispatcher.handlers[ApplicationDispatcher.STATE_START]
        pause_handler_d = Deferred()
        unpause_handler_d = Deferred()

        @inlineCallbacks
        def pause_handler(*args, **kw):
            pause_handler_d.callback(None)
            yield unpause_handler_d
            resp = yield orig_handler(*args, **kw)
            returnValue(resp)

        dispatcher.handlers[ApplicationDispatcher.STATE_START] = pause_handler

        # msg sent from user
        self.ch("transport").make_dispatch_inbound(
            None, from_addr='123', transport_name='transport')
        yield pause_handler_d

        # assert that the created session data is correct, then unpause
        yield self.assert_session('123', {
            'state': ApplicationDispatcher.STATE_START,
        })
        unpause_handler_d.callback(None)

        # assert that the user received a response
        [msg] = yield self.ch("transport").wait_for_dispatched_outbound()
        self.assertEqual(msg['content'],
                         'Please select a choice.\n1) Flappy Bird')
        # assert that session data updated correctly
        yield self.assert_session('123', {
            'state': ApplicationDispatcher.STATE_SELECT,
            'endpoints': '["flappy-bird"]',
        })

    @inlineCallbacks
    def test_inbound_event_routing(self):
        dispatcher = yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'active_endpoint': 'flappy-bird',
            'endpoints': '["flappy-bird"]',
        })
        yield dispatcher.cache_outbound_user_id(
            'message_id', '123')

        event = yield self.ch('transport').make_dispatch_ack(
            {'message_id': 'message_id'})
        self.assert_rkeys_used('transport.event', 'app1.event')
        self.assert_dispatched_endpoint(
            event, 'default', self.ch('app1').get_dispatched_events())

    @inlineCallbacks
    def test_event_routing_without_active_endpoint(self):
        dispatcher = yield self.get_dispatcher()

        yield self.setup_session('123', {
            'state': ApplicationDispatcher.STATE_SELECTED,
            'endpoints': '["flappy-bird"]',
        })
        yield dispatcher.cache_outbound_user_id(
            'message_id', '123')

        yield self.ch('transport').make_dispatch_ack(
            {'message_id': 'message_id'})
        self.assert_rkeys_used('transport.event')
        self.assertEqual(
            self.ch('app1').get_dispatched_events(), [])
