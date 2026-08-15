package com.skeleton.home.update

object HomeUpdatePolicy {
    const val ProductionPackage = "com.skeleton.home"
    const val UpdatePath = "/api/native/app-update"
    const val UpdateSchema = "skeleton.home.native_app_update.v1"

    fun isNewer(installedVersionCode: Int, remoteVersionCode: Int): Boolean =
        remoteVersionCode > installedVersionCode

    fun validSha256(value: String): Boolean =
        value.length == 64 && value.all { it in '0'..'9' || it in 'a'..'f' }

    fun configuredBaseUrls(raw: String): List<String> =
        raw.split(';')
            .map { it.trim().trimEnd('/') }
            .filter { it.startsWith("http://") || it.startsWith("https://") }
            .filter { it.length <= 512 }
            .distinct()

    fun validApkPath(path: String): Boolean =
        path.startsWith('/') && !path.startsWith("//") && ".." !in path.split('/')
}
