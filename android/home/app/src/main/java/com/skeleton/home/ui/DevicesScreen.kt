package com.skeleton.home.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.skeleton.home.domain.DeviceInventoryOrdering
import com.skeleton.home.domain.DeviceInventoryRepository
import com.skeleton.home.domain.HomeDevice

@Composable
fun DevicesScreen(
    padding: PaddingValues,
    repository: DeviceInventoryRepository,
) {
    val grouped = DeviceInventoryOrdering.group(repository.loadDeviceInventory())
    val uriHandler = LocalUriHandler.current

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding),
        contentPadding = PaddingValues(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            Text(
                "Пристрої",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.SemiBold,
            )
        }

        if (grouped.isEmpty()) {
            item {
                Text("Немає доступних даних про пристрої")
            }
        } else {
            grouped.forEach { residenceGroup ->
                item(key = "residence-${residenceGroup.residence.id}") {
                    Text(
                        residenceGroup.residence.displayName,
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                items(
                    items = residenceGroup.devices,
                    key = { it.id },
                ) { device ->
                    val webUi = DeviceInventoryOrdering.confirmedWebUi(device)
                    DeviceCard(
                        device = device,
                        webUiAvailable = webUi != null,
                        onOpenWebUi = {
                            if (webUi != null) {
                                uriHandler.openUri(webUi)
                            }
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun DeviceCard(
    device: HomeDevice,
    webUiAvailable: Boolean,
    onOpenWebUi: () -> Unit,
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (webUiAvailable) {
                IconButton(
                    onClick = onOpenWebUi,
                    modifier = Modifier.semantics {
                        contentDescription = "device-web-ui-${device.id}"
                    },
                ) {
                    Icon(Icons.Filled.OpenInBrowser, contentDescription = null)
                }
            } else {
                Icon(
                    Icons.Filled.Devices,
                    contentDescription = null,
                )
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(device.displayName, fontWeight = FontWeight.Medium)
            }
        }
    }
}
