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
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.LiveTv
import androidx.compose.material.icons.filled.SettingsRemote
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.skeleton.home.data.SyntheticHomeRepository
import com.skeleton.home.domain.AuthSessionProvider
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.navigation.PrimaryBottomRoutes
import com.skeleton.home.navigation.canNavigateTo

@Composable
fun HomeApp(session: AuthSessionProvider) {
    MaterialTheme {
        Surface(color = MaterialTheme.colorScheme.background) {
            HomeShell(session = session)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeShell(
    session: AuthSessionProvider,
    repository: SyntheticHomeRepository = SyntheticHomeRepository(),
) {
    val currentSession = session.currentSession()
    var currentRoute by remember { mutableStateOf<HomeRoute>(HomeRoute.Home) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Home") })
        },
        bottomBar = {
            NavigationBar {
                PrimaryBottomRoutes.forEach { route ->
                    NavigationBarItem(
                        selected = currentRoute == route,
                        onClick = { currentRoute = route },
                        icon = { Icon(route.icon(), contentDescription = route.label) },
                        label = { Text(route.label) },
                    )
                }
            }
        },
    ) { padding ->
        when (currentRoute) {
            HomeRoute.Home -> HomeScreen(
                padding = padding,
                canShowOperatorHub = session.canAccessOperatorHub(currentSession),
                onOperatorHub = {
                    if (canNavigateTo(HomeRoute.OperatorHub, currentSession, session)) {
                        currentRoute = HomeRoute.OperatorHub
                    }
                },
                onRemote = { currentRoute = HomeRoute.Remote },
            )
            HomeRoute.Video -> PlaceholderScreen("Відео", padding)
            HomeRoute.Devices -> PlaceholderScreen("Пристрої", padding)
            HomeRoute.Remote -> PlaceholderScreen("Пульт", padding)
            HomeRoute.OperatorHub -> {
                if (canNavigateTo(HomeRoute.OperatorHub, currentSession, session)) {
                    OperatorHubScreen(padding)
                } else {
                    currentRoute = HomeRoute.Home
                    HomeScreen(
                        padding = padding,
                        canShowOperatorHub = false,
                        onOperatorHub = {},
                        onRemote = { currentRoute = HomeRoute.Remote },
                    )
                }
            }
        }
    }
}

@Composable
fun HomeScreen(
    padding: PaddingValues,
    canShowOperatorHub: Boolean,
    onOperatorHub: () -> Unit,
    onRemote: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Головна", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text(
            "Синтетичний режим. Живі значення, пристрої та дії очікують канонічне джерело.",
            style = MaterialTheme.typography.bodyLarge,
        )
        Button(onClick = onRemote) {
            Icon(Icons.Filled.SettingsRemote, contentDescription = "Пульт")
            Spacer(modifier = Modifier.size(8.dp))
            Text("Пульт")
        }
        if (canShowOperatorHub) {
            Button(
                modifier = Modifier.semantics { contentDescription = "operator-hub-entry" },
                onClick = onOperatorHub,
            ) {
                Icon(Icons.Filled.Hub, contentDescription = "СК")
                Spacer(modifier = Modifier.size(8.dp))
                Text("СК")
            }
        }
    }
}

@Composable
fun OperatorHubScreen(padding: PaddingValues) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Icon(Icons.Filled.Hub, contentDescription = "СК", modifier = Modifier.size(32.dp))
            Text("СК", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        }
        Text("Операторський контур зарезервовано без живих підключень.")
    }
}

@Composable
fun PlaceholderScreen(title: String, padding: PaddingValues) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(title, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text("Плейсхолдер без живих Home-даних.")
        Spacer(modifier = Modifier.height(8.dp))
    }
}

private fun HomeRoute.icon(): ImageVector = when (this) {
    HomeRoute.Home -> Icons.Filled.Home
    HomeRoute.Video -> Icons.Filled.LiveTv
    HomeRoute.Devices -> Icons.Filled.Devices
    HomeRoute.Remote -> Icons.Filled.SettingsRemote
    HomeRoute.OperatorHub -> Icons.Filled.Hub
}
