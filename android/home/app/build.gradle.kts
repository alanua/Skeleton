val homeEdgeBaseUrls = providers.gradleProperty("homeEdgeBaseUrls").orElse("").get()
val escapedHomeEdgeBaseUrls = homeEdgeBaseUrls.replace("\\", "\\\\").replace("\"", "\\\"")

plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android {
    namespace = "com.skeleton.home"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.skeleton.home"
        minSdk = 26
        targetSdk = 34
        versionCode = 130
        versionName = "1.4.15"
        buildConfigField("String", "HOME_EDGE_BASE_URLS", "\"$escapedHomeEdgeBaseUrls\"")
    }
    buildFeatures { compose = true; buildConfig = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
    kotlinOptions { jvmTarget = "17" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}
dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.06.00")
    implementation(composeBom)
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.health.connect:connect-client:1.1.0-alpha08")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
