package com.skeleton.home

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.ui.HomeApp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            HomeApp(session = SyntheticSession.operator())
        }
    }
}
