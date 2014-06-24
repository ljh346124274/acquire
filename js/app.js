define(function(require) {
	var $ = require('jquery'),
		enums = require('enums'),
		network = require('network'),
		pubsub = require('pubsub'),
		current_page = null,
		show_page = function(page) {
			if (page !== current_page) {
				$('.page').hide();
				$('#page-' + page).show();

				current_page = page;
			}
		};

	require('lobby');
	require('game');

	if (network.isBrowserSupported()) {
		show_page('login');
	} else {
		show_page('websocket-not-supported');
	}

	pubsub.subscribe('network-open', function() {
		show_page('lobby');
	});

	pubsub.subscribe('network-close', function() {
		show_page('login');
	});

	pubsub.subscribe('network-error', function() {
		var $message;

		$message = $('<p>').text('Could not connect to the server.');
		$('#login-error-message').html($message);
		show_page('login');
	});

	pubsub.subscribe('server-FatalError', function(fatal_error_id) {
		var message, $message;

		if (fatal_error_id === enums.FatalErrors.InvalidUsername) {
			message = 'Invalid username.';
		} else if (fatal_error_id === enums.FatalErrors.UsernameAlreadyInUse) {
			message = 'Username already in use.';
		} else {
			message = 'Unknown error.';
		}

		$message = $('<p>').text(message);
		$('#login-error-message').html($message);
	});

	pubsub.subscribe('client-JoinGame', function() {
		show_page('game');
	});

	pubsub.subscribe('client-LeaveGame', function() {
		show_page('lobby');
	});

	$('#login-form').submit(function() {
		show_page('connecting');
		$('#login-error-message').text('');
		network.connect($('#login-form-username').val());

		return false;
	});
});
