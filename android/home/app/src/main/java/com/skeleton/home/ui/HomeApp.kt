package com.skeleton.home.ui

import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Box
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
import androidx.compose.runtime.withFrameMillis
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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
import com.skeleton.home.domain.MediaSourceSearchStatus
import com.skeleton.home.domain.MediaSourceSearchUiState
import com.skeleton.home.domain.WorkDescriptionAutoScroll
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.navigation.bottomRoutesFor
import com.skeleton.home.navigation.canNavigateTo
import com.skeleton.home.update.HomeUpdateAction
import com.skeleton.home.update.HomeUpdateManager
import kotlinx.coroutines.delay

@Composable
fun HomeApp(
    session: AuthSessionProvider,
    initialRoute: HomeRoute = HomeRoute.Home,
    updateManager: HomeUpdateManager? = null,
) {
    MaterialTheme {
        Surface(color = MaterialTheme.colorScheme.background) {
            HomeShell(session = session, initialRoute = initialRoute, updateManager = updateManager)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeShell(
    session: AuthSessionProvider,
    initialRoute: HomeRoute = HomeRoute.Home,
    repository: SyntheticHomeRepository = SyntheticHomeRepository(),
    updateManager: HomeUpdateManager? = null,
) {
    val currentSession = session.currentSession()
    val bottomRoutes = bottomRoutesFor(currentSession, session)
    var currentRoute by remember { mutableStateOf(initialRoute) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Home") },
                actions = { updateManager?.let { HomeUpdateAction(it) } },
            )
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
            HomeRoute.Video -> VideoScreen(padding)
            HomeRoute.Devices -> DevicesScreen(padding, repository)
            HomeRoute.Remote -> PlaceholderScreen("Пульт", padding)
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
fun VideoScreen(padding: PaddingValues) {
    var searchState by remember {
        mutableStateOf(MediaSourceSearchUiState(status = MediaSourceSearchStatus.IDLE))
    }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Відео", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
        Text(searchState.message ?: "Пошук релізу")
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            MediaScopeButton(
                contentDescription = "media-series-season-control",
                icon = SeriesSeasonIcon,
                label = "Сезон",
            )
            MediaScopeButton(
                contentDescription = "media-episode-control",
                icon = EpisodeIcon,
                label = "Епізод",
            )
        }
        WorkDescriptionText(
            description = "Опис релізу прокручується вгору лише тоді, коли текст довший за область перегляду.",
        )
        Button(
            onClick = { searchState = searchState.retry() },
            modifier = Modifier.semantics {
                contentDescription = "media-search-retry"
            },
        ) {
            Text("Спробувати ще раз")
        }
    }
}

@Composable
private fun MediaScopeButton(
    contentDescription: String,
    icon: ImageVector,
    label: String,
) {
    OutlinedButton(
        onClick = {},
        modifier = Modifier.semantics {
            this.contentDescription = contentDescription
        },
    ) {
        Icon(icon, contentDescription = null)
        Spacer(modifier = Modifier.size(8.dp))
        Text(label)
    }
}

@Composable
fun WorkDescriptionText(
    description: String,
    modifier: Modifier = Modifier,
) {
    val scrollState = rememberScrollState()
    LaunchedEffect(description, scrollState) {
        var idleUntil = 0L
        while (true) {
            val now = withFrameMillis { it }
            val maxOffset = scrollState.maxValue
            if (!WorkDescriptionAutoScroll.shouldAnimate(maxOffset + 1, 1)) {
                delay(160)
                continue
            }
            if (scrollState.isScrollInProgress) {
                idleUntil = now + WorkDescriptionAutoScroll.IdleGraceMillis
                delay(80)
                continue
            }
            if (now < idleUntil) {
                delay(80)
                continue
            }
            if (scrollState.value >= maxOffset) {
                delay(WorkDescriptionAutoScroll.EndPauseMillis)
                scrollState.scrollTo(0)
                continue
            }
            val step = WorkDescriptionAutoScroll.nextOffset(scrollState.value, maxOffset)
            scrollState.scrollTo(step.offset)
            delay(40)
        }
    }
    Box(
        modifier = modifier
            .height(96.dp)
            .verticalScroll(scrollState)
            .semantics { contentDescription = "work-description-auto-scroll" },
    ) {
        Text(description, style = MaterialTheme.typography.bodyMedium)
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

private val SeriesSeasonIcon: ImageVector by lazy {
    ImageVector.Builder(
        name = "SeriesSeasonStack",
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    ).apply {
        path(
            fill = SolidColor(Color.Black),
            pathFillType = PathFillType.NonZero,
        ) {
            moveTo(4f, 6f)
            quadTo(4f, 4.9f, 4.9f, 4f)
            lineTo(17f, 4f)
            quadTo(18.1f, 4f, 19f, 4.9f)
            lineTo(19f, 14f)
            quadTo(19f, 15.1f, 18.1f, 16f)
            lineTo(6f, 16f)
            quadTo(4.9f, 16f, 4f, 15.1f)
            close()
            moveTo(7f, 18f)
            lineTo(20f, 18f)
            lineTo(20f, 7f)
            lineTo(22f, 7f)
            lineTo(22f, 18f)
            quadTo(22f, 19.1f, 21.1f, 20f)
            lineTo(7f, 20f)
            close()
            moveTo(7f, 7f)
            lineTo(7f, 13f)
            lineTo(16f, 10f)
            close()
        }
    }.build()
}

private val EpisodeIcon: ImageVector by lazy {
    ImageVector.Builder(
        name = "EpisodeSinglePlay",
        defaultWidth = 24.dp,
        defaultHeight = 24.dp,
        viewportWidth = 24f,
        viewportHeight = 24f,
    ).apply {
        path(
            fill = SolidColor(Color.Black),
            pathFillType = PathFillType.NonZero,
        ) {
            moveTo(5f, 4f)
            quadTo(3.9f, 4f, 3f, 4.9f)
            lineTo(3f, 19.1f)
            quadTo(3.9f, 20f, 5f, 20f)
            lineTo(19f, 20f)
            quadTo(20.1f, 20f, 21f, 19.1f)
            lineTo(21f, 4.9f)
            quadTo(20.1f, 4f, 19f, 4f)
            close()
            moveTo(10f, 8f)
            lineTo(10f, 16f)
            lineTo(16.5f, 12f)
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
