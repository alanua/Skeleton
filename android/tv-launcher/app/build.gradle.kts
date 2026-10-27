plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android {
    namespace = "com.skeleton.tvlauncher"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.skeleton.tvlauncher"
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "0.2.0"
        buildConfigField("String","HOME_EDGE","\"http://192.168.1.54:8100\"")
    }
    buildFeatures { compose = true; buildConfig = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
    kotlinOptions { jvmTarget = "17" }
    compileOptions { sourceCompatibility=JavaVersion.VERSION_17; targetCompatibility=JavaVersion.VERSION_17 }
}
dependencies {
    val bom=platform("androidx.compose:compose-bom:2024.06.00")
    implementation(bom)
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
