package com.skeleton.home.auth

import com.skeleton.home.domain.AuthSessionProvider
import com.skeleton.home.domain.HomeSession
import com.skeleton.home.domain.UserRole

class SyntheticSession private constructor(
    private val session: HomeSession,
) : AuthSessionProvider {
    override fun currentSession(): HomeSession = session

    override fun canAccessOperatorHub(session: HomeSession): Boolean =
        session.role == UserRole.OPERATOR

    companion object {
        fun operator(): SyntheticSession =
            SyntheticSession(HomeSession("synthetic-operator", UserRole.OPERATOR))

        fun ordinary(): SyntheticSession =
            SyntheticSession(HomeSession("synthetic-ordinary", UserRole.ORDINARY))

        fun spouse(): SyntheticSession =
            SyntheticSession(HomeSession("synthetic-spouse", UserRole.SPOUSE))
    }
}
