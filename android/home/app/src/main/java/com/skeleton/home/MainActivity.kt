package com.skeleton.home

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.ui.HomeApp
import com.skeleton.home.update.HomeUpdateManager

class MainActivity : ComponentActivity() {
    private lateinit var homeUpdateManager: HomeUpdateManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        homeUpdateManager = HomeUpdateManager(this)
        setContent {
            HomeApp(session = SyntheticSession.operator(), updateManager = homeUpdateManager)
        }
    }

    override fun onResume() {
        super.onResume()
        if (::homeUpdateManager.isInitialized) homeUpdateManager.onResume()
    }
}
