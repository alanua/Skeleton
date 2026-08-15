package com.skeleton.home.update

import android.widget.Toast
import androidx.compose.foundation.layout.Box
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import kotlinx.coroutines.launch

@Composable
fun HomeUpdateAction(manager: HomeUpdateManager) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var expanded by remember { mutableStateOf(false) }
    var candidate by remember { mutableStateOf<HomeUpdateInfo?>(null) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }

    LaunchedEffect(manager) {
        if (manager.canCheckForUpdates) candidate = manager.latestAvailable()
    }

    Box {
        IconButton(
            onClick = { expanded = true },
            modifier = androidx.compose.ui.Modifier.semantics { contentDescription = "home-overflow-menu" },
        ) {
            Icon(Icons.Filled.MoreVert, contentDescription = "Меню")
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(
                text = { Text("Оновити застосунок") },
                leadingIcon = { Icon(Icons.Filled.Refresh, contentDescription = null) },
                onClick = {
                    expanded = false
                    if (!manager.canCheckForUpdates) {
                        Toast.makeText(context, "Канал оновлень недоступний у цій збірці", Toast.LENGTH_LONG).show()
                    } else {
                        scope.launch {
                            val latest = manager.latestAvailable()
                            if (latest == null) {
                                Toast.makeText(context, "У вас уже остання версія Home", Toast.LENGTH_SHORT).show()
                            } else {
                                candidate = latest
                            }
                        }
                    }
                },
            )
        }
    }

    candidate?.let { info ->
        AlertDialog(
            onDismissRequest = { if (!busy) candidate = null },
            title = { Text("Доступне оновлення") },
            text = {
                Text(if (busy) status.ifBlank { "Готую оновлення…" } else "Home ${info.versionName}. APK буде перевірено перед системним встановленням.")
            },
            dismissButton = {
                TextButton(onClick = { candidate = null }, enabled = !busy) { Text("Пізніше") }
            },
            confirmButton = {
                Button(
                    enabled = !busy,
                    onClick = {
                        scope.launch {
                            busy = true
                            status = "Готую завантаження…"
                            runCatching { manager.downloadAndRequestInstall(info) { status = it } }
                                .onSuccess { candidate = null }
                                .onFailure { error ->
                                    status = error.message ?: "Оновлення не вдалося"
                                    Toast.makeText(context, status, Toast.LENGTH_LONG).show()
                                }
                            busy = false
                        }
                    },
                ) {
                    if (busy) CircularProgressIndicator() else Text("Оновити")
                }
            },
        )
    }
}
