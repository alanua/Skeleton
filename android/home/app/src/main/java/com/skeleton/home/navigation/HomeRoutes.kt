package com.skeleton.home.navigation

import com.skeleton.home.domain.AuthSessionProvider
import com.skeleton.home.domain.HomeSession

sealed class HomeRoute(val route: String, val label: String) {
    data object Home : HomeRoute("home", "Головна")
    data object Video : HomeRoute("video", "Відео")
    data object Devices : HomeRoute("devices", "Пристрої")
    data object Remote : HomeRoute("remote", "Пульт")
    data object OperatorHub : HomeRoute("operator-hub", "СК")
}

val PrimaryBottomRoutes = listOf(
    HomeRoute.Home,
    HomeRoute.Video,
    HomeRoute.Devices,
)

fun bottomRoutesFor(
    session: HomeSession,
    auth: AuthSessionProvider,
): List<HomeRoute> =
    if (auth.canAccessOperatorHub(session)) {
        PrimaryBottomRoutes + HomeRoute.OperatorHub
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
