"""Executable source contracts, not Kotlin execution or Android/hardware validation.

Synthetic command fixtures check required guard ordering and effect boundaries in the
actual Kotlin source. JVM behavior tests live beside the Android unit tests and are
intentionally not run in the source-only environment.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'android/home/app/src/main/java/com/skeleton/home/travel/TravelMediaSession.kt'
JVM_TEST = ROOT / 'android/home/app/src/test/java/com/skeleton/home/travel/TravelMediaSessionTest.kt'


class TravelMediaSessionContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.execute = cls.source.split('fun execute(', 1)[1].split('/** Explicit output', 1)[0]

    def test_synthetic_command_rejections_precede_player_effects(self):
        fixtures = {
            'closed': 'if (closed)',
            'wrong_session': 'command.sessionId != current.sessionId',
            'remote_disabled': 'command.origin == TravelOrigin.REMOTE && !ownerAuthenticated && !remoteEnabled',
            'stale_revision': 'command.expectedRevision != current.revision',
            'missing_media': 'command.action == TravelAction.LOAD && command.media == null',
            'invalid_position': 'command.positionMs == null || command.positionMs < 0',
            'media_not_ready': 'current.media == null || current.playback in',
        }
        for reason, guard in fixtures.items():
            with self.subTest(synthetic_rejection=reason):
                self.assertIn(guard, self.execute)
                self.assertLess(self.execute.index(f'reject("{reason}")'), self.execute.index('player.load('))

    def test_every_action_has_real_player_effect_before_acknowledgement(self):
        for action, effect in [('LOAD', 'load'), ('PLAY', 'play'), ('PAUSE', 'pause'),
                               ('SEEK', 'seekTo'), ('STOP', 'stop')]:
            with self.subTest(action=action):
                branch = self.execute.split(f'TravelAction.{action} -> {{', 1)[1].split('\n                }', 1)[0]
                self.assertIn(f'player.{effect}(', branch)
                self.assertLess(branch.index(f'player.{effect}('), branch.index('current.copy('))
        self.assertIn('requested.coerceAtMost(it)', self.execute)
        self.assertIn('commit(next)', self.execute)

    def test_disconnect_and_selection_never_autoplay(self):
        output = self.source.split('fun selectOutput(', 1)[1].split('fun close()', 1)[0]
        self.assertIn('reject("hdmi_unavailable")', output)
        self.assertIn('player.stop()', output)
        self.assertIn('output = TravelOutput.PHONE', output)
        self.assertIn('media = null', output)
        self.assertNotIn('player.play()', output)
        self.assertNotIn('player.load(', output)

    def test_failure_lifecycle_and_transport_isolation(self):
        self.assertIn('private var remoteEnabled = false', self.source)
        self.assertIn('private var remoteOwner: Any? = null', self.source)
        self.assertIn('if (enabled)', self.source)
        self.assertIn('check(remoteOwner == null)', self.source)
        self.assertIn('else {\n            revokeRemoteControl()', self.source)
        self.assertIn('if (closed || !remoteEnabled || remoteOwner != null) return false', self.source)
        self.assertLess(self.source.index('remoteEnabled = false\n        remoteOwner = owner'),
                        self.source.index('return true', self.source.index('fun claimRemoteControl')))
        self.assertIn('if (remoteOwner === owner) remoteOwner = null', self.source)
        self.assertIn('if (remoteOwner !== owner) return reject("remote_disabled")', self.source)
        self.assertIn('if (!available) revokeRemoteControl()', self.source)
        self.assertLess(self.source.index('if (!available) revokeRemoteControl()'),
                        self.source.index('if (available == current.hdmiAvailable)'))
        self.assertIn('private fun revokeRemoteControl() {\n        remoteEnabled = false\n        remoteOwner = null',
                      self.source)
        self.assertIn('closed = true\n        remoteEnabled = false\n        remoteOwner = null', self.source)
        self.assertIn('runCatching { player.stop() }', self.source)
        self.assertIn('error = "player_failure"', self.source)
        self.assertIn('revision = current.revision + 1', self.source)
        self.assertIn('runCatching { remote.publish(current) }', self.source)
        self.assertNotIn('import android.', self.source)
        for forbidden in ['http://', 'Runtime.getRuntime', '/api/', 'MainActivity']:
            self.assertNotIn(forbidden, self.source)

    def test_source_validation_and_future_jvm_scenarios(self):
        self.assertIn('parsed.scheme == "content" || parsed.scheme == "https"', self.source)
        self.assertIn('parsed.rawUserInfo == null', self.source)
        self.assertIn('require(durationMs == null || durationMs >= 0)', self.source)
        tests = JVM_TEST.read_text()
        for scenario in ['localAndRemoteShareRevisionAndPlayer',
                         'hdmiDisconnectStopsWithoutAutoplayAndCloseRevokesCommands',
                         'playerFailureIsSanitizedAndRequiresReload',
                         'explicitRevokeCancelsPendingAndActiveRemoteAuthority',
                         'hdmiDisconnectRevokesRemoteAuthorityEvenWhenPlayerStopFails']:
            self.assertIn('@Test fun ' + scenario, tests)
        self.assertIn('content://synthetic/demo', tests)
        self.assertIn('https://example.test/demo', tests)


if __name__ == '__main__':
    unittest.main()
