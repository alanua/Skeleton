package com.skeleton.home.navigation

import com.skeleton.home.domain.AuthSessionProvider
import com.skeleton.home.domain.HomeSession

sealed class HomeRoute(val route: String, val label: String) {
    data object Home : HomeRoute("home", "Головна")
    data object Video : HomeRoute("video", "Відео")
    data object Devices : HomeRoute("devices", "Пристрої")
    data object Remote : HomeRoute("remote", "Пульт")
    data object OperatorHub : HomeRoute("operator-hub", "СК")

    companion object {
        fun fromRoute(route: String): HomeRoute =
            AllRoutes.firstOrNull { it.route == route } ?: Home
    }
}

val PrimaryBottomRoutes = listOf(
    HomeRoute.Home,
    HomeRoute.Video,
    HomeRoute.Devices,
)

val OperatorBottomRoutes = PrimaryBottomRoutes + HomeRoute.OperatorHub

private val AllRoutes = listOf(
    HomeRoute.Home,
    HomeRoute.Video,
    HomeRoute.Devices,
    HomeRoute.Remote,
    HomeRoute.OperatorHub,
)

fun bottomRoutesFor(
    session: HomeSession,
    auth: AuthSessionProvider,
): List<HomeRoute> =
    if (auth.canAccessOperatorHub(session)) {
        OperatorBottomRoutes
    } else {
        PrimaryBottomRoutes
    }

fun canNavigateTo(
    route: HomeRoute,
    session: HomeSession,
    auth: AuthSessionProvider,
): Boolean = when (route) {
    HomeRoute.OperatorHub -> auth.canAccessOperatorHub(session)
    HomeRoute.Home,
    HomeRoute.Video,
    HomeRoute.Devices,
    HomeRoute.Remote,
    -> true
}
