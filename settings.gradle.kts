rootProject.name = "MaoerLite"
enableFeaturePreview("TYPESAFE_PROJECT_ACCESSORS")

// Prefer local SDK's aapt2 when available to avoid downloading the
// platform-specific `com.android.tools.build:aapt2:*` from Maven.
run {
    if (System.getProperty("android.aapt2FromMavenOverride").isNullOrBlank()) {
        val localPropertiesFile = file("local.properties")
        if (localPropertiesFile.isFile) {
            val properties = java.util.Properties().apply {
                localPropertiesFile.inputStream().use(::load)
            }

            val sdkDirValue = properties.getProperty("sdk.dir")?.takeIf { it.isNotBlank() }
            val sdkDir = sdkDirValue?.let(::file)?.takeIf { it.isDirectory }
            val buildToolsDir = sdkDir?.resolve("build-tools")?.takeIf { it.isDirectory }

            fun versionWeight(name: String): Int {
                val parts = name.split('.').map { it.toIntOrNull() ?: 0 }
                val major = parts.getOrElse(0) { 0 }
                val minor = parts.getOrElse(1) { 0 }
                val patch = parts.getOrElse(2) { 0 }
                return major * 1_000_000 + minor * 1_000 + patch
            }

            val bestBuildTools = buildToolsDir
                ?.listFiles()
                ?.filter { it.isDirectory }
                ?.maxByOrNull { versionWeight(it.name) }

            val aapt2Name = if (System.getProperty("os.name").contains("Windows", ignoreCase = true)) {
                "aapt2.exe"
            } else {
                "aapt2"
            }

            val aapt2 = bestBuildTools?.resolve(aapt2Name)?.takeIf { it.isFile }
            if (aapt2 != null) {
                System.setProperty("android.aapt2FromMavenOverride", aapt2.absolutePath)
            }
        }
    }
}

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

include(":composeApp")
