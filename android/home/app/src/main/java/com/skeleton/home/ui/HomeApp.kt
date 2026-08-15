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
import androidx.compose.material.icons.filled.LiveTv
import androidx.compose.material.icons.filled.SettingsRemote
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.skeleton.home.data.HomeEdgeEndpointConfig
import com.skeleton.home.data.HomeEdgeOperatorDashboardRepository
import com.skeleton.home.data.SyntheticHomeRepository
import com.skeleton.home.domain.AuthSessionProvider
import com.skeleton.home.domain.OperatorLiveState
import com.skeleton.home.domain.OperatorLiveStateStatus
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.navigation.bottomRoutesFor
import com.skeleton.home.navigation.canNavigateTo

@Composable
fun HomeApp(
    session: AuthSessionProvider,
    initialRoute: HomeRoute = HomeRoute.Home,
) {
    MaterialTheme {
        Surface(color = MaterialTheme.colorScheme.background) {
            HomeShell(session = session, initialRoute = initialRoute)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeShell(
    session: AuthSessionProvider,
    initialRoute: HomeRoute = HomeRoute.Home,
    repository: SyntheticHomeRepository = SyntheticHomeRepository(),
) {
    val currentSession = session.currentSession()
    val bottomRoutes = bottomRoutesFor(currentSession, session)
    var currentRoute by remember { mutableStateOf(initialRoute) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text("Home") })
        },
        bottomBar = {
            NavigationBar {
                bottomRoutes.forEach { route ->
                    NavigationBarItem(
                        selected = currentRoute == route,
                        onClick = { currentRoute = route },
                        modifier = Modifier.semantics {
                            contentDescription = "bottom-nav-${route.route}"
                        },
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
                onRemote = { currentRoute = HomeRoute.Remote },
            )
            HomeRoute.Video -> PlaceholderScreen("Відео", padding)
            HomeRoute.Devices -> PlaceholderScreen("Пристрої", padding)
            HomeRoute.Remote -> PlaceholderScreen("Пульт", padding)
            HomeRoute.OperatorHub -> {
                if (canNavigateTo(HomeRoute.OperatorHub, currentSession, session)) {
                    val context = LocalContext.current
                    val dashboardRepository = remember(context) {
                        HomeEdgeOperatorDashboardRepository(HomeEdgeEndpointConfig.from(context))
                    }
                    OperatorHubScreen(padding, dashboardRepository)
                } else {
                    AccessDeniedScreen("СК", padding)
                }
            }
        }
    }
}

@Composable
fun HomeScreen(
    padding: PaddingValues,
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
        Seek15Controls()
        Button(onClick = onRemote) {
            Icon(Icons.Filled.SettingsRemote, contentDescription = "Пульт")
            Spacer(modifier = Modifier.size(8.dp))
            Text("Пульт")
        }
    }
}

@Composable
private fun Seek15Controls() {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Seek15Button(
            contentDescription = "control-back-15",
            icon = SeekBack15Icon,
            label = "15 с",
            modifier = Modifier.weight(1f),
        )
        Seek15Button(
            contentDescription = "control-forward-15",
            icon = SeekForward15Icon,
            label = "15 с",
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun Seek15Button(
    contentDescription: String,
    icon: ImageVector,
    label: String,
    modifier: Modifier = Modifier,
) {
    OutlinedButton(
        onClick = {},
        modifier = modifier.semantics {
            this.contentDescription = contentDescription
        },
    ) {
        Icon(icon, contentDescription = null)
        Spacer(modifier = Modifier.size(8.dp))
        Text(label)
    }
}

@Composable
fun OperatorHubScreen(
    padding: PaddingValues,
    repository: HomeEdgeOperatorDashboardRepository,
) {
    var refreshKey by remember { mutableStateOf(0) }
    var liveState by remember {
        mutableStateOf<OperatorLiveState?>(null)
    }

    LaunchedEffect(repository, refreshKey) {
        liveState = repository.loadLiveState()
    }

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
            Icon(MaterialHubIcon, contentDescription = "СК", modifier = Modifier.size(32.dp))
            Text("СК", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        }
        Button(onClick = { refreshKey += 1 }) {
            Text("Оновити")
        }
        when (val state = liveState) {
            null -> Text("Завантаження стану...")
            else -> OperatorLiveStateContent(state)
        }
    }
}

@Composable
private fun OperatorLiveStateContent(state: OperatorLiveState) {
    when (state.status) {
        OperatorLiveStateStatus.CURRENT -> {
            Text("Стан: поточний")
            Text("Оновлено: ${formatAge(state)}")
        }
        OperatorLiveStateStatus.STALE -> {
            Text("Стан: застарілий")
            Text("Останній живий зріз: ${formatAge(state)}")
        }
        OperatorLiveStateStatus.OFFLINE -> {
            Text("Стан: OFFLINE")
            Text("Причина: ${state.offlineReason ?: "маршрут недоступний"}")
        }
    }
    state.sections.forEach { section ->
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(section.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            Text(section.value)
            section.detail?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
        }
    }
}

private fun formatAge(state: OperatorLiveState): String {
    val observedAt = state.observedAtEpochSeconds ?: return "немає живого зрізу"
    val ageSeconds = (state.checkedAtEpochSeconds - observedAt).coerceAtLeast(0)
    return if (state.status == OperatorLiveStateStatus.CURRENT && ageSeconds < 5) {
        "щойно"
    } else {
        "$ageSeconds с тому"
    }
}

@Composable
fun AccessDeniedScreen(title: String, padding: PaddingValues) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(title, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text("Доступ до розділу відхилено для цього синтетичного профілю.")
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
    HomeRoute.OperatorHub -> MaterialHubIcon
}

private val SeekBack15Icon: ImageVector by lazy {
    ImageVector.Builder(
        name = "SeekBack15",
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    ).apply {
        path(
            fill = SolidColor(Color.Black),
            pathFillType = PathFillType.NonZero,
        ) {
            moveTo(11f, 4f)
            lineTo(6f, 8f)
            lineTo(11f, 12f)
            lineTo(11f, 9.1f)
            quadTo(13.9f, 9.15f, 15.95f, 11.15f)
            quadTo(18f, 13.15f, 18f, 16f)
            quadTo(18f, 18.1f, 16.9f, 19.7f)
            quadTo(15.8f, 21.3f, 13.9f, 22f)
            lineTo(12.9f, 20.05f)
            quadTo(14.35f, 19.5f, 15.18f, 18.42f)
            quadTo(16f, 17.35f, 16f, 16f)
            quadTo(16f, 14f, 14.55f, 12.6f)
            quadTo(13.1f, 11.2f, 11f, 11.1f)
            lineTo(11f, 14f)
            lineTo(4f, 8f)
            lineTo(11f, 2f)
            lineTo(11f, 4f)
            close()
        }
    }.build()
}

private val SeekForward15Icon: ImageVector by lazy {
    ImageVector.Builder(
        name = "SeekForward15",
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    ).apply {
        path(
            fill = SolidColor(Color.Black),
            pathFillType = PathFillType.NonZero,
        ) {
            moveTo(13f, 4f)
            lineTo(18f, 8f)
            lineTo(13f, 12f)
            lineTo(13f, 9.1f)
            quadTo(10.1f, 9.15f, 8.05f, 11.15f)
            quadTo(6f, 13.15f, 6f, 16f)
            quadTo(6f, 18.1f, 7.1f, 19.7f)
            quadTo(8.2f, 21.3f, 10.1f, 22f)
            lineTo(11.1f, 20.05f)
            quadTo(9.65f, 19.5f, 8.82f, 18.42f)
            quadTo(8f, 17.35f, 8f, 16f)
            quadTo(8f, 14f, 9.45f, 12.6f)
            quadTo(10.9f, 11.2f, 13f, 11.1f)
            lineTo(13f, 14f)
            lineTo(20f, 8f)
            lineTo(13f, 2f)
            lineTo(13f, 4f)
            close()
        }
    }.build()
}

private val MaterialHubIcon: ImageVector by lazy {
    ImageVector.Builder(
        name = "MaterialHub",
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    ).apply {
        path(
            fill = SolidColor(Color.Black),
            pathFillType = PathFillType.NonZero,
        ) {
            moveTo(7f, 21f)
            quadTo(5.75f, 21f, 4.875f, 20.125f)
            quadTo(4f, 19.25f, 4f, 18f)
            quadTo(4f, 17.05f, 4.55f, 16.275f)
            quadTo(5.1f, 15.5f, 6f, 15.2f)
            lineTo(6f, 8.8f)
            quadTo(5.1f, 8.5f, 4.55f, 7.725f)
            quadTo(4f, 6.95f, 4f, 6f)
            quadTo(4f, 4.75f, 4.875f, 3.875f)
            quadTo(5.75f, 3f, 7f, 3f)
            quadTo(7.95f, 3f, 8.725f, 3.55f)
            quadTo(9.5f, 4.1f, 9.8f, 5f)
            lineTo(14.2f, 5f)
            quadTo(14.5f, 4.1f, 15.275f, 3.55f)
            quadTo(16.05f, 3f, 17f, 3f)
            quadTo(18.25f, 3f, 19.125f, 3.875f)
            quadTo(20f, 4.75f, 20f, 6f)
            quadTo(20f, 6.95f, 19.45f, 7.725f)
            quadTo(18.9f, 8.5f, 18f, 8.8f)
            lineTo(18f, 15.2f)
            quadTo(18.9f, 15.5f, 19.45f, 16.275f)
            quadTo(20f, 17.05f, 20f, 18f)
            quadTo(20f, 19.25f, 19.125f, 20.125f)
            quadTo(18.25f, 21f, 17f, 21f)
            quadTo(16.05f, 21f, 15.275f, 20.45f)
            quadTo(14.5f, 19.9f, 14.2f, 19f)
            lineTo(9.8f, 19f)
            quadTo(9.5f, 19.9f, 8.725f, 20.45f)
            quadTo(7.95f, 21f, 7f, 21f)
            close()
            moveTo(7f, 19f)
            quadTo(7.425f, 19f, 7.713f, 18.713f)
            quadTo(8f, 18.425f, 8f, 18f)
            quadTo(8f, 17.575f, 7.713f, 17.288f)
            quadTo(7.425f, 17f, 7f, 17f)
            quadTo(6.575f, 17f, 6.288f, 17.288f)
            quadTo(6f, 17.575f, 6f, 18f)
            quadTo(6f, 18.425f, 6.288f, 18.713f)
            quadTo(6.575f, 19f, 7f, 19f)
            close()
            moveTo(17f, 19f)
            quadTo(17.425f, 19f, 17.713f, 18.713f)
            quadTo(18f, 18.425f, 18f, 18f)
            quadTo(18f, 17.575f, 17.713f, 17.288f)
            quadTo(17.425f, 17f, 17f, 17f)
            quadTo(16.575f, 17f, 16.288f, 17.288f)
            quadTo(16f, 17.575f, 16f, 18f)
            quadTo(16f, 18.425f, 16.288f, 18.713f)
            quadTo(16.575f, 19f, 17f, 19f)
            close()
            moveTo(8f, 15.2f)
            quadTo(8.65f, 15.425f, 9.125f, 15.9f)
            quadTo(9.6f, 16.375f, 9.8f, 17f)
            lineTo(14.2f, 17f)
            quadTo(14.4f, 16.375f, 14.875f, 15.9f)
            quadTo(15.35f, 15.425f, 16f, 15.2f)
            lineTo(16f, 8.8f)
            quadTo(15.35f, 8.575f, 14.875f, 8.1f)
            quadTo(14.4f, 7.625f, 14.2f, 7f)
            lineTo(9.8f, 7f)
            quadTo(9.6f, 7.625f, 9.125f, 8.1f)
            quadTo(8.65f, 8.575f, 8f, 8.8f)
            lineTo(8f, 15.2f)
            close()
            moveTo(7f, 7f)
            quadTo(7.425f, 7f, 7.713f, 6.713f)
            quadTo(8f, 6.425f, 8f, 6f)
            quadTo(8f, 5.575f, 7.713f, 5.288f)
            quadTo(7.425f, 5f, 7f, 5f)
            quadTo(6.575f, 5f, 6.288f, 5.288f)
            quadTo(6f, 5.575f, 6f, 6f)
            quadTo(6f, 6.425f, 6.288f, 6.713f)
            quadTo(6.575f, 7f, 7f, 7f)
            close()
            moveTo(17f, 7f)
            quadTo(17.425f, 7f, 17.713f, 6.713f)
            quadTo(18f, 6.425f, 18f, 6f)
            quadTo(18f, 5.575f, 17.713f, 5.288f)
            quadTo(17.425f, 5f, 17f, 5f)
            quadTo(16.575f, 5f, 16.288f, 5.288f)
            quadTo(16f, 5.575f, 16f, 6f)
            quadTo(16f, 6.425f, 16.288f, 6.713f)
            quadTo(16.575f, 7f, 17f, 7f)
            close()
        }
    }.build()
}
