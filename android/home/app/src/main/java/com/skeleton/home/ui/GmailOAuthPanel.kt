package com.skeleton.home.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.skeleton.home.homeedge.GmailOAuthHomeEdgeApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@Composable
fun GmailOAuthPanel(
    api: GmailOAuthHomeEdgeApi = remember { GmailOAuthHomeEdgeApi() },
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var returnedUrl by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Gmail потребує авторизації з правами modify + send.") }
    var busy by remember { mutableStateOf(false) }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text("Home Edge → Secrets", style = MaterialTheme.typography.titleLarge)

        Button(
            enabled = !busy,
            onClick = {
                busy = true
                scope.launch {
                    runCatching { withContext(Dispatchers.IO) { api.start() } }
                        .onSuccess { result ->
                            status = result.message
                            runCatching {
                                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(result.authorizationUrl)))
                            }.onFailure {
                                status = "Google URL отримано, але браузер не відкрився."
                            }
                        }
                        .onFailure { error ->
                            status = error.message ?: "Не вдалося почати авторизацію Gmail"
                        }
                    busy = false
                }
            },
            modifier = Modifier.semantics { contentDescription = "gmail-oauth-start" },
        ) {
            Text("Авторизувати Gmail (телефон)")
        }

        OutlinedTextField(
            value = returnedUrl,
            onValueChange = { returnedUrl = it },
            modifier = Modifier
                .fillMaxWidth()
                .semantics { contentDescription = "gmail-oauth-returned-url" },
            label = { Text("Вставити адресу після Google") },
            minLines = 2,
        )

        Button(
            enabled = !busy && returnedUrl.isNotBlank(),
            onClick = {
                busy = true
                scope.launch {
                    runCatching { withContext(Dispatchers.IO) { api.complete(returnedUrl) } }
                        .onSuccess { result ->
                            status = result.message
                            returnedUrl = ""
                        }
                        .onFailure { error ->
                            status = error.message ?: "Не вдалося завершити авторизацію Gmail"
                        }
                    busy = false
                }
            },
            modifier = Modifier.semantics { contentDescription = "gmail-oauth-complete" },
        ) {
            Text("Завершити авторизацію")
        }

        Text(status, modifier = Modifier.semantics { contentDescription = "gmail-oauth-status" })
        Text(
            "Надсилання та переміщення в кошик — лише після явного дозволу. Остаточне видалення заборонене.",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
