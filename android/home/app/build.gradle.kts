import java.net.URI

val homeEdgeBaseUrls = providers.gradleProperty("homeEdgeBaseUrls").orElse("").get()
val escapedHomeEdgeBaseUrls = homeEdgeBaseUrls.replace("\\", "\\\\").replace("\"", "\\\"")
val homeEdgeSecretBaseUrl = providers.gradleProperty("homeEdgeSecretBaseUrl").orElse("").get().trim()
val escapedHomeEdgeSecretBaseUrl = homeEdgeSecretBaseUrl.replace("\\", "\\\\").replace("\"", "\\\"")
val homeEdgeSecretTailscaleIp = providers.gradleProperty("homeEdgeSecretTailscaleIp").orElse("").get().trim()
val escapedHomeEdgeSecretTailscaleIp = homeEdgeSecretTailscaleIp.replace("\\", "\\\\").replace("\"", "\\\"")

fun validateHomeEdgeReleaseBaseUrls(raw: String) {
    val urls = raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }
    require(urls.isNotEmpty()) {
        "assembleRelease requires -PhomeEdgeBaseUrls with at least one HTTPS or HTTP base URL"
    }
    urls.forEach { url ->
        val parsed = runCatching { URI(url) }.getOrNull()
        require(parsed != null && parsed.scheme in setOf("https", "http") && !parsed.host.isNullOrBlank()) {
            "assembleRelease requires every homeEdgeBaseUrls entry to be a valid HTTPS or HTTP base URL"
        }
    }
}

gradle.taskGraph.whenReady {
    if (allTasks.any { it.name == "assembleRelease" }) {
        validateHomeEdgeReleaseBaseUrls(homeEdgeBaseUrls)
        val secretUrl = runCatching { URI(homeEdgeSecretBaseUrl) }.getOrNull()
        require(secretUrl != null && secretUrl.scheme == "https" && !secretUrl.host.isNullOrBlank()) {
            "assembleRelease requires -PhomeEdgeSecretBaseUrl with one valid HTTPS base URL"
        }
        require(Regex("""(?:\d{1,3}\.){3}\d{1,3}""").matches(homeEdgeSecretTailscaleIp)) {
            "assembleRelease requires -PhomeEdgeSecretTailscaleIp with one IPv4 address"
        }
    }
}

plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android {
    namespace = "com.skeleton.home"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.skeleton.home"
        minSdk = 26
        targetSdk = 34
        versionCode = 163
        versionName = "1.4.47"
        buildConfigField("String", "HOME_EDGE_BASE_URLS", "\"$escapedHomeEdgeBaseUrls\"")
        buildConfigField("String", "HOME_EDGE_SECRET_BASE_URL", "\"$escapedHomeEdgeSecretBaseUrl\"")
        buildConfigField("String", "HOME_EDGE_SECRET_TAILSCALE_IP", "\"$escapedHomeEdgeSecretTailscaleIp\"")
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
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
