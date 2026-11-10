"""Source-only transport boundaries; JVM behavior scenarios require the Android toolchain."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'android/home/app/src/main/java/com/skeleton/home/travel/TravelSecureLink.kt'


class TravelSecureLinkContract(unittest.TestCase):
    def test_no_activation_or_secret_storage(self):
        source = SOURCE.read_text()
        for forbidden in ('java.net.Socket', 'ServerSocket', 'android.', 'SharedPreferences',
                          'FileOutputStream', 'MainActivity', 'DatagramSocket'):
            self.assertNotIn(forbidden, source)
        self.assertIn('fun interface TravelRecordExchange', source)

    def test_authenticated_bounded_directional_records(self):
        source = SOURCE.read_text()
        for required in ('AES/GCM/NoPadding', 'HmacSHA256', 'GCMParameterSpec(128, nonce)',
                         'record.size in 28..MAX_RECORD', 'sequence == received + 1',
                         'it.updateAAD(', 'sendKey.fill(0)', 'receiveKey.fill(0)',
                         'require(it.available() == 0)', 'TravelOrigin.REMOTE'):
            self.assertIn(required, source)
        self.assertLess(source.index('.doFinal(record,'), source.index('received = sequence'))
        self.assertIn('TravelWire.command(channel.open(request))', source)
        self.assertIn('session.claimRemoteControl(owner)', source)
        self.assertIn('session.executeRemote(owner, command)', source)
        self.assertIn('session.releaseRemoteControl(owner)', source)

    def test_behavior_scenarios_present(self):
        tests = (ROOT / 'android/home/app/src/test/java/com/skeleton/home/travel/TravelSecureLinkTest.kt').read_text()
        for scenario in ('encryptedRoundTripUsesAuthoritativePlayerAndRevision',
                         'authenticationReplayReflectionAndBounds',
                         'malformedAuthenticatedCommandClosesHostBeforePlayerEffects',
                         'secureHostFailsClosedUntilExplicitlyOptedIn',
                         'overlappingAuthenticatedHostIsRejectedAndStaleCloseCannotRevokeNewOwner'):
            self.assertIn('@Test fun ' + scenario, tests)
