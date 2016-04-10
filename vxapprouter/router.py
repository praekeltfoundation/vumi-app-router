# -*- test-case-name: vxapprouter.tests.test_router -*-
import json

from twisted.internet.defer import inlineCallbacks

from vumi import log
from vumi.dispatchers.endpoint_dispatchers import Dispatcher
from vumi.components.session import SessionManager
from vumi.config import ConfigDict, ConfigList, ConfigInt, ConfigText
from vumi.message import TransportUserMessage


class ApplicationDispatcherConfig(Dispatcher.CONFIG_CLASS):

    # Static configuration
    session_expiry = ConfigInt(
        ("Maximum amount of time in seconds to keep session data around. "
         "Defaults to 5 minutes."),
        default=5 * 60, static=True)
    redis_manager = ConfigDict(
        "Redis client configuration.", default={}, static=True)
    # Dynamic, per-message configuration
    menu_title = ConfigDict(
        "Content for the menu title",
        default={'content': "Please select a choice."})
    entries = ConfigList(
        "A list of application endpoints and associated labels",
        default=[])
    invalid_input_message = ConfigText(
        "Prompt to display when warning about an invalid choice",
        default=("That is an incorrect choice. Please enter the number "
                 "of the menu item you wish to choose.\n\n 1) Try Again"))
    error_message = ConfigText(
        ("Prompt to display when a configuration change invalidates "
         "an active session."),
        default=("Oops! We experienced a temporary error. "
                 "Please try and dial the line again."))


class StateResponse(object):
    def __init__(self, state, session_update=None, inbound=(), outbound=()):
        self.next_state = state
        self.session_update = session_update or {}
        self.inbound = inbound
        self.outbound = outbound


def mkmenu(options, start=1, format='%s) %s'):
    items = [format % (idx, opt) for idx, opt in enumerate(options, start)]
    return '\n'.join(items)


class ApplicationDispatcher(Dispatcher):
    CONFIG_CLASS = ApplicationDispatcherConfig
    worker_name = 'application_dispatcher'

    STATE_START = "start"
    STATE_SELECT = "select"
    STATE_SELECTED = "selected"
    STATE_BAD_INPUT = "bad_input"

    def setup_dispatcher(self):
        self.handlers = {
            self.STATE_START: self.handle_state_start,
            # self.STATE_SELECT: self.handle_state_select,
            # self.STATE_SELECTED: self.handle_state_selected,
            # self.STATE_BAD_INPUT: self.handle_state_bad_input,
        }
        return super(ApplicationDispatcher, self).setup_dispatcher()

    def session_manager(self, config):
        return SessionManager.from_redis_config(
            config.redis_manager,
            key_prefix=self.worker_name,
            max_session_length=config.session_expiry)

    def handle_state_start(self, config, session, msg):
        """
        When presenting the menu, we also store the list of endpoints
        in the session data. Later, in the select state, we load
        these endpoints and retrieve the candidate endpoint based
        on the user's menu choice.
        """
        reply_msg = msg.reply(self.create_menu(config))
        endpoints = json.dumps(
            [entry['endpoint'] for entry in config.entries]
        )
        return StateResponse(
            self.STATE_SELECT, {'endpoints': endpoints}, outbound=[reply_msg])

    # def handle_state_select(self, config, session, msg):
    #     endpoint = self.get_endpoint_for_choice(msg, session)
    #     if endpoint is None:
    #         reply_msg = msg.reply(config.invalid_input_message)
    #         return StateResponse(self.STATE_BAD_INPUT, outbound=[reply_msg])
    #
    #     if endpoint not in self.target_endpoints(config):
    #         log.msg(("Router configuration change forced session "
    #                  "termination for user %s" % msg['from_addr']))
    #         error_reply_msg = self.make_error_reply(msg, config)
    #         return StateResponse(None, outbound=[error_reply_msg])
    #
    #     forwarded_msg = self.forwarded_message(
    #         msg, content=None,
    #         session_event=TransportUserMessage.SESSION_NEW)
    #     log.msg("Switched to endpoint '%s' for user %s" %
    #             (endpoint, msg['from_addr']))
    #     return StateResponse(
    #         self.STATE_SELECTED, {'active_endpoint': endpoint},
    #         inbound=[(forwarded_msg, endpoint)])
    #
    # def handle_state_selected(self, config, session, msg):
    #     active_endpoint = session['active_endpoint']
    #     if active_endpoint not in self.target_endpoints(config):
    #         log.msg(("Router configuration change forced session "
    #                  "termination for user %s" % msg['from_addr']))
    #         error_reply_msg = self.make_error_reply(msg, config)
    #         return StateResponse(None, outbound=[error_reply_msg])
    #     else:
    #         return StateResponse(
    #             self.STATE_SELECTED, inbound=[(msg, active_endpoint)])
    #
    # def handle_state_bad_input(self, config, session, msg):
    #     choice = self.get_menu_choice(msg, (1, 1))
    #     if choice is None:
    #         reply_msg = msg.reply(config.invalid_input_message)
    #         return StateResponse(self.STATE_BAD_INPUT, outbound=[reply_msg])
    #     else:
    #         return self.handle_state_start(config, session, msg)

    def create_menu(self, config):
        labels = [entry['label'] for entry in config.entries]
        return (config.menu_title['content'] + "\n" + mkmenu(labels))

    def make_error_reply(self, msg, config):
        return msg.reply(config.error_message, continue_session=False)

    @inlineCallbacks
    def process_inbound(self, config, msg, connector_name):
        log.msg("Processing inbound message: %s" % (msg,))
        user_id = msg['from_addr']
        session_manager = yield self.session_manager(config)
        session = yield session_manager.load_session(user_id)
        session_event = msg['session_event']
        if not session or session_event == TransportUserMessage.SESSION_NEW:
            log.msg("Creating session for user %s" % user_id)
            session = {}
            state = self.STATE_START
            yield session_manager.create_session(user_id, state=state)
        elif session_event == TransportUserMessage.SESSION_CLOSE:
            yield self.handle_session_close(config, session, msg)
            return
        else:
            log.msg("Loading session for user %s: %s" % (user_id, session,))
            state = session['state']

        try:
            # We must assume the state handlers might be async, even if the
            # current implementations aren't. There is at least one test that
            # depends on asynchrony here to hook into the state transition.
            state_resp = yield self.handlers[state](config, session, msg)
            if state_resp.next_state is None:
                # Session terminated (right now, just in the case of a
                # administrator-initiated configuration change
                yield session_manager.clear_session(user_id)
            else:
                session['state'] = state_resp.next_state
                session.update(state_resp.session_update)
                if state != state_resp.next_state:
                    log.msg("State transition for user %s: %s => %s" %
                            (user_id, state, state_resp.next_state))
                yield session_manager.save_session(user_id, session)
            for msg, endpoint in state_resp.inbound:
                yield self.publish_inbound(msg, connector_name, endpoint)
            for msg in state_resp.outbound:
                yield self.process_outbound(config, msg, connector_name)
        except:
            log.err()
            yield session_manager.clear_session(user_id)
            yield self.publish_outbound(self.make_error_reply(msg, config))

    def process_outbound(self, config, msg, connector_name):
        return self.publish_outbound(
            msg, connector_name, msg.get_routing_endpoint())

    def process_event(self, config, event, connector_name):
        target = self.find_target(config, event, connector_name)
        if target is None:
            return
        return self.publish_event(event, target[0], target[1])
