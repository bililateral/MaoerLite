package com.maoer.lite

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import cafe.adriel.voyager.navigator.Navigator
import com.maoer.lite.ui.home.HomeScreen

/** Android initializes its application-scoped dependencies in MaoerApplication. */
@Composable
fun App() {
    MaterialTheme { Navigator(HomeScreen) }
}
