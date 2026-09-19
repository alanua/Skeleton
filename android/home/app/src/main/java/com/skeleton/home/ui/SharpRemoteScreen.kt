package com.skeleton.home.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.VerifiedActionState
import com.skeleton.home.homeedge.SharpManagedDisplayHomeEdgeApi
import com.skeleton.home.homeedge.SharpTvAction
import com.skeleton.home.homeedge.SharpTvStatus
import kotlinx.coroutines.launch

@Composable
fun DevicesScreen(
    padding: PaddingValues,
    onSharpRemote: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Пристрої", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Surface(
            tonalElevation = 2.dp,
            shape = MaterialTheme.shapes.large,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text("Sharp TV", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                Text("Керований екран · HDMI через Home Edge")
                Button(
                    onClick = onSharpRemote,
                    modifier = Modifier.semantics { contentDescription = "sharp-tv-open-remote" },
                ) {
                    Text("Пульт ТВ")
                }
            }
        }
    }
}

@Composable
fun SharpRemoteScreen(
    padding: PaddingValues,
    api: SharpManagedDisplayHomeEdgeApi? = null,
) {
    val client = api ?: remember { SharpManagedDisplayHomeEdgeApi() }
    val scope = rememberCoroutineScope()
    var status by remember { mutableStateOf<SharpTvStatus?>(null) }
    var statusMessage by remember { mutableStateOf("Перевіряю Sharp TV…") }
    var actionState by remember { mutableStateOf<VerifiedActionState?>(null) }

    suspend fun refresh() {
        runCatching { client.status() }
            .onSuccess { current ->
                status = current
                statusMessage = current.statusLabel()
            }
            .onFailure {
                status = SharpTvStatus(
                    connectivity = ConnectivityStatus.OFFLINE,
                    reachable = false,
                    currentSource = null,
                    supportsHdmi3 = false,
                    powerOnAvailable = false,
                )
                statusMessage = "Sharp TV недоступний"
            }
    }

    fun send(action: SharpTvAction) {
        scope.launch {
            runCatching { client.action(action) }
                .onSuccess { result ->
                    actionState = result.state
                    statusMessage = result.message
                    if (action != SharpTvAction.STANDBY && action != SharpTvAction.POWER_ON) {
                        refresh()
                    }
                }
                .onFailure {
                    actionState = null
                    statusMessage = "Команду не виконано"
                    refresh()
                }
        }
    }

    LaunchedEffect(client) { refresh() }

    val reachable = status?.reachable == true
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Пульт ТВ", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text("Sharp TV", style = MaterialTheme.typography.titleMedium)
        Text(statusMessage, modifier = Modifier.semantics { contentDescription = "sharp-tv-status" })
        status?.currentSource?.let { source -> Text("Джерело: ${source.uppercase()}") }
        actionState?.let { state -> Text("Стан дії: ${state.uiLabel()}") }

        Text("Джерело", fontWeight = FontWeight.SemiBold)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            RemoteButton("HDMI 1", "sharp-tv-hdmi1", reachable, Modifier.weight(1f)) { send(SharpTvAction.HDMI1) }
            RemoteButton("HDMI 2", "sharp-tv-hdmi2", reachable, Modifier.weight(1f)) { send(SharpTvAction.HDMI2) }
            if (status?.supportsHdmi3 == true) {
                RemoteButton("HDMI 3", "sharp-tv-hdmi3", reachable, Modifier.weight(1f)) { send(SharpTvAction.HDMI3) }
            }
        }

        Spacer(Modifier.height(2.dp))
        Text("Навігація", fontWeight = FontWeight.SemiBold)
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
            RemoteButton("↑", "sharp-tv-key-up", reachable) { send(SharpTvAction.UP) }
        }
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
            RemoteButton("←", "sharp-tv-key-left", reachable) { send(SharpTvAction.LEFT) }
            RemoteButton("OK", "sharp-tv-key-ok", reachable) { send(SharpTvAction.OK) }
            RemoteButton("→", "sharp-tv-key-right", reachable) { send(SharpTvAction.RIGHT) }
        }
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
            RemoteButton("↓", "sharp-tv-key-down", reachable) { send(SharpTvAction.DOWN) }
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            RemoteButton("Назад", "sharp-tv-key-back", reachable, Modifier.weight(1f)) { send(SharpTvAction.BACK) }
            RemoteButton("Меню", "sharp-tv-key-menu", reachable, Modifier.weight(1f)) { send(SharpTvAction.MENU) }
        }

        Spacer(Modifier.height(4.dp))
        OutlinedButton(
            onClick = { send(SharpTvAction.STANDBY) },
            enabled = reachable,
            modifier = Modifier
                .fillMaxWidth()
                .semantics { contentDescription = "sharp-tv-standby" },
        ) {
            Text("Standby")
        }
        if (status?.powerOnAvailable == true) {
            Button(
                onClick = { send(SharpTvAction.POWER_ON) },
                modifier = Modifier
                    .fillMaxWidth()
                    .semantics { contentDescription = "sharp-tv-power-on" },
            ) {
                Text("Увімкнути")
            }
        }
    }
}

@Composable
private fun RemoteButton(
    label: String,
    description: String,
    enabled: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.semantics { contentDescription = description },
    ) {
        Text(label)
    }
}

private fun SharpTvStatus.statusLabel(): String = when (connectivity) {
    ConnectivityStatus.ONLINE -> "Sharp TV доступний"
    ConnectivityStatus.DEGRADED -> "Sharp TV: зв’язок нестабільний"
    ConnectivityStatus.OFFLINE -> "Sharp TV недоступний"
}

private fun VerifiedActionState.uiLabel(): String = when (this) {
    VerifiedActionState.SENT -> "надіслано"
    VerifiedActionState.ACCEPTED -> "прийнято"
    VerifiedActionState.APPLIED -> "застосовано"
    VerifiedActionState.PHYSICALLY_VERIFIED -> "підтверджено"
}
