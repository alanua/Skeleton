"""Public-safe source contracts; behavioral security tests live in the JVM suite."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / 'android/home/app/src/main/java/com/skeleton/home/travel'
TEST = ROOT / 'android/home/app/src/test/java/com/skeleton/home/travel'


class TravelLanTransportContract(unittest.TestCase):
    def test_opt_in_and_network_bounds(self):
        source = (MAIN / 'TravelLanTransport.kt').read_text()
        for required in ('require(optIn', 'timeoutMs in 100..30000',
                         'InetSocketAddress(address, 0), 1', 'listener.soTimeout = timeoutMs',
                         'socket.connect(', 'timer.schedule({ close() }',
                         'size in 28..TravelSecureChannel.MAX_RECORD', 'check(!closed && !used)'):
            self.assertIn(required, source)
        self.assertLess(source.index('require(size in'), source.index('ByteArray(size)'))

    def test_authentication_dispatch_and_cleanup(self):
        source = (MAIN / 'TravelLanTransport.kt').read_text()
        for required in ('active.open(connection!!.read()).contentEquals(HELLO)',
                         'check(remoteControlOptIn)', 'TravelSecureHost(session, active)',
                         'dispatch { response = secure!!.exchange(request) }',
                         'dispatch { secure?.close() }', 'key?.fill(0)',
                         'channel?.close()', 'timer.shutdownNow()', 'invite.close()'):
            self.assertIn(required, source)
        self.assertLess(source.index('active.open('), source.index('TravelSecureHost(session'))
        self.assertNotIn('session.allowRemoteControl(true)', source)

    def test_no_secret_storage_or_automatic_activation(self):
        source = '\n'.join((MAIN / name).read_text() for name in
                           ('TravelLanPairing.kt', 'TravelLanTransport.kt'))
        for forbidden in ('android.', 'SharedPreferences', 'FileOutputStream', 'println(',
                          'Log.', 'DatagramSocket', 'MainActivity', 'http://', 'https://'):
            self.assertNotIn(forbidden, source)
        pairing = (MAIN / 'TravelLanPairing.kt').read_text()
        for required in ('SecureRandom()', 'ByteArray(32)', 'secret.fill(0)',
                         'TravelLanPairing(redacted)', 'InetAddress.getByAddress',
                         'payload.length <= 180'):
            self.assertIn(required, pairing)

    def test_loopback_security_scenarios_present(self):
        source = (TEST / 'TravelLanTransportTest.kt').read_text()
        for scenario in ('loopbackRoundTripDisconnectRevokesAndInviteIsSingleUse',
                         'malformedLengthsTruncationTamperAndReplayFailBeforePlayerEffects',
                         'stalledPeerAndOwnerCloseUnblockAndRevoke',
                         'explicitOptInAndBoundedConfigurationRequired',
                         'hdmiDisconnectResetsPermissionAndBlocksStaleLanPacket',
                         'revokedGrantBeforeLateHelloCannotBeRestoredByStaleHost',
                         'hdmiLossBeforeLateHelloCannotBeRestoredByStaleHost'):
            self.assertIn('@Test fun ' + scenario, source)
        for kind in ('negative', 'oversize', 'truncated', 'tamper', 'wrongKey', 'reflection', 'replay'):
            self.assertIn('"' + kind + '"', source)
        self.assertIn('127.0.0.1', source)
        self.assertIn('session.allowRemoteControl(true)\n        session.allowRemoteControl(false)', source)
        self.assertIn('session.allowRemoteControl(true)\n        session.updateHdmiAvailability(false)', source)
        self.assertGreaterEqual(source.count('fail("permission restored")'), 2)
        self.assertTrue((TEST / 'TravelLanPairingTest.kt').exists())
