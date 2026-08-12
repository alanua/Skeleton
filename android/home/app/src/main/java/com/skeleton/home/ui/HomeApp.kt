package com.skeleton.home.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cast
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Forward15
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.LiveTv
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material.icons.filled.Replay15
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material.icons.filled.Tv
import androidx.compose.material.icons.filled.VolumeOff
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.AssistChip
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.skeleton.home.data.SyntheticHomeRepository
import com.skeleton.home.domain.AuthSessionProvider
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.HomeControlCapability
import com.skeleton.home.domain.HomeControlSurfaceState
import com.skeleton.home.domain.HomeMode
import com.skeleton.home.domain.HomeModeContext
import com.skeleton.home.domain.HomePlaceholderState
import com.skeleton.home.domain.VerifiedActionState
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.navigation.bottomRoutesFor
import com.skeleton.home.navigation.canNavigateTo

@Composable
fun HomeApp(
    session: AuthSessionProvider,
    initialRoute: HomeRoute = HomeRoute.Home,
) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            background = HomeBlack,
            surface = Panel,
            surfaceVariant = PanelRaised,
            primary = HomeOrange,
            secondary = WarmText,
            onBackground = WarmText,
            onSurface = WarmText,
            onSurfaceVariant = MutedText,
        ),
    ) {
        Surface(color = MaterialTheme.colorScheme.background) {
            HomeShell(session = session, initialRoute = initialRoute)
        }
    }
}

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
        bottomBar = {
            NavigationBar(containerColor = Panel) {
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
                state = repository.placeholderState(currentSession),
            )
            HomeRoute.Video -> PlaceholderScreen("Відео", padding)
            HomeRoute.Devices -> PlaceholderScreen("Пристрої", padding)
            HomeRoute.OperatorHub -> {
                if (canNavigateTo(HomeRoute.OperatorHub, currentSession, session)) {
                    OperatorHubScreen(padding)
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
    state: HomePlaceholderState,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        HomeStatusRow(state)
        ModeRow(state.surface.modes)
        ActiveMediaCard(state.surface)
        PlaybackProgress(state.surface)
        AdaptiveControls(state.surface)
    }
}

@Composable
private fun HomeStatusRow(state: HomePlaceholderState) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .semantics { contentDescription = "home-top-status-row" },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text("Home", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(7.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(state.connectivityStatus.statusColor()),
                )
                Text(
                    state.connectivityStatus.name,
                    style = MaterialTheme.typography.labelMedium,
                    color = MutedText,
                )
                Text(
                    state.actionState?.name ?: VerifiedActionState.SENT.name,
                    style = MaterialTheme.typography.labelMedium,
                    color = MutedText,
                )
            }
        }
        IconButton(onClick = {}, modifier = Modifier.semantics { contentDescription = "home-control-power" }) {
            Icon(Icons.Filled.PowerSettingsNew, contentDescription = null)
        }
        IconButton(onClick = {}, modifier = Modifier.semantics { contentDescription = "home-control-more" }) {
            Icon(Icons.Filled.MoreVert, contentDescription = null)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ModeRow(modes: List<HomeModeContext>) {
    FlowRow(
        modifier = Modifier
            .fillMaxWidth()
            .semantics { contentDescription = "home-mode-row" },
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        modes.forEach { mode ->
            AssistChip(
                onClick = {},
                enabled = mode.isAvailable,
                modifier = Modifier
                    .widthIn(min = 72.dp)
                    .heightIn(min = 44.dp)
                    .alpha(if (mode.isAvailable) 1f else 0.46f)
                    .semantics {
                        contentDescription = "home-mode-${mode.label}"
                    },
                leadingIcon = {
                    Icon(mode.mode.icon(), contentDescription = null, modifier = Modifier.size(18.dp))
                },
                label = { Text(mode.label, maxLines = 1) },
            )
        }
    }
}

@Composable
private fun ActiveMediaCard(surface: HomeControlSurfaceState) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .semantics { contentDescription = "home-active-media-card" },
        color = PanelRaised,
        shape = RoundedCornerShape(28.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .weight(0.42f)
                    .widthIn(min = 96.dp, max = 180.dp)
                    .aspectRatio(0.72f)
                    .clip(RoundedCornerShape(18.dp))
                    .background(ArtworkBase)
                    .border(1.dp, ArtworkStroke, RoundedCornerShape(18.dp))
                    .semantics { contentDescription = "home-artwork-placeholder" },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    surface.activeMedia.artworkLabel,
                    modifier = Modifier.padding(12.dp),
                    style = MaterialTheme.typography.labelMedium,
                    color = MutedText,
                )
            }
            Column(
                modifier = Modifier.weight(0.58f),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    surface.activeMedia.title,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(surface.activeMedia.year, style = MaterialTheme.typography.bodyMedium, color = MutedText)
                surface.activeMedia.seasonEpisodeLine?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = MutedText)
                }
            }
        }
    }
}

@Composable
private fun PlaybackProgress(surface: HomeControlSurfaceState) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .semantics { contentDescription = "home-playback-progress" },
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        LinearProgressIndicator(
            progress = { surface.playback.progress.coerceIn(0f, 1f) },
            modifier = Modifier
                .fillMaxWidth()
                .height(6.dp)
                .clip(RoundedCornerShape(3.dp)),
            color = HomeOrange,
            trackColor = PanelRaised,
        )
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(surface.playback.positionLabel, style = MaterialTheme.typography.labelMedium, color = MutedText)
            Text(surface.playback.durationLabel, style = MaterialTheme.typography.labelMedium, color = MutedText)
        }
    }
}

@Composable
private fun AdaptiveControls(surface: HomeControlSurfaceState) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .semantics { contentDescription = "home-adaptive-controls" },
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceEvenly,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            ControlButton(HomeControlCapability.SEEK_BACK_15, surface, "control-back-15") {
                Icon(Icons.Filled.Replay15, contentDescription = null)
            }
            ControlButton(HomeControlCapability.PLAY_PAUSE, surface, "control-play-pause", prominent = true) {
                Icon(
                    if (surface.playback.isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                    contentDescription = null,
                )
            }
            ControlButton(HomeControlCapability.SEEK_FORWARD_15, surface, "control-forward-15") {
                Icon(Icons.Filled.Forward15, contentDescription = null)
            }
            ControlButton(HomeControlCapability.MUTE, surface, "control-mute") {
                Icon(
                    if (surface.playback.isMuted) Icons.Filled.VolumeOff else Icons.Filled.VolumeUp,
                    contentDescription = null,
                )
            }
        }
        if (HomeControlCapability.VOLUME in surface.capabilities) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .semantics { contentDescription = "home-volume-control" },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Icon(Icons.Filled.VolumeUp, contentDescription = null, tint = MutedText)
                Slider(
                    value = surface.playback.volume.coerceIn(0f, 1f),
                    onValueChange = {},
                    modifier = Modifier.weight(1f),
                )
                Text("${(surface.playback.volume * 100).toInt()}%", style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}

@Composable
private fun ControlButton(
    capability: HomeControlCapability,
    surface: HomeControlSurfaceState,
    description: String,
    prominent: Boolean = false,
    icon: @Composable () -> Unit,
) {
    if (capability !in surface.capabilities) {
        return
    }
    Surface(
        color = if (prominent) HomeOrange else PanelRaised,
        contentColor = if (prominent) Color.Black else WarmText,
        shape = CircleShape,
    ) {
        IconButton(
            onClick = {},
            modifier = Modifier
                .size(if (prominent) 64.dp else 52.dp)
                .semantics { contentDescription = description },
        ) {
            icon()
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
            Icon(MaterialHubIcon, contentDescription = "СК", modifier = Modifier.size(32.dp))
            Text("СК", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        }
        Text("Огляд стану")
        Text("Зв'язок: ONLINE / DEGRADED / OFFLINE")
        Text("Дії: SENT / ACCEPTED / APPLIED / PHYSICALLY_VERIFIED")
        Text("Операторський контур зарезервовано без живих підключень.")
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
    HomeRoute.OperatorHub -> MaterialHubIcon
}

private fun HomeMode.icon(): ImageVector = when (this) {
    HomeMode.YOUTUBE -> Icons.Filled.PlayArrow
    HomeMode.CAST -> Icons.Filled.Cast
    HomeMode.TV -> Icons.Filled.Tv
    HomeMode.GAMES -> Icons.Filled.SportsEsports
}

private fun ConnectivityStatus.statusColor(): Color = when (this) {
    ConnectivityStatus.ONLINE -> Color(0xFF4AD07D)
    ConnectivityStatus.DEGRADED -> Color(0xFFFFB84D)
    ConnectivityStatus.OFFLINE -> Color(0xFFFF6A3D)
}

private val HomeBlack = Color(0xFF090909)
private val Panel = Color(0xFF151515)
private val PanelRaised = Color(0xFF22201E)
private val HomeOrange = Color(0xFFFF7A1A)
private val WarmText = Color(0xFFF4EFE7)
private val MutedText = Color(0xFFAEA79E)
private val ArtworkBase = Color(0xFF302A24)
private val ArtworkStroke = Color(0xFF554536)

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
