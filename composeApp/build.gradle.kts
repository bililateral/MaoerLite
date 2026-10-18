import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import groovy.json.JsonSlurper
import groovy.json.JsonOutput

plugins {
    alias(libs.plugins.kotlinMultiplatform)
    alias(libs.plugins.androidApplication)
    alias(libs.plugins.composeMultiplatform) // 对应 TOML 中的 composeMultiplatform
    alias(libs.plugins.composeCompiler)
    alias(libs.plugins.kotlinSerialization)
}

abstract class GenerateErrorCatalog : DefaultTask() {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val catalog: RegularFileProperty
    @get:OutputDirectory
    abstract val destination: DirectoryProperty

    @TaskAction
    fun generate() {
        @Suppress("UNCHECKED_CAST")
        val entries = JsonSlurper().parse(catalog.get().asFile) as Map<String, String>
        require(entries.isNotEmpty() && entries.all { (key, value) -> key.matches(Regex("[a-z][a-z0-9_]*")) && value.isNotBlank() })
        fun literal(value: String) = JsonOutput.toJson(value).replace("$", "\\$")
        val target = destination.get().file("com/maoer/lite/data/error/GeneratedErrorCatalog.kt").asFile
        target.parentFile.mkdirs()
        target.writeText("// Generated from agent-server/maoer_agent/resources/error_catalog.json. Do not edit.\n" +
            "package com.maoer.lite.data.error\n\ninternal val errorMessages: Map<String, String> = mapOf(\n" +
            entries.entries.joinToString(",\n") { (key, value) -> "    ${literal(key)} to ${literal(value)}" } + "\n)\n")
    }
}
val generatedErrors = layout.buildDirectory.dir("generated/errorCatalog/kotlin")
val generateErrorCatalog by tasks.registering(GenerateErrorCatalog::class) {
    catalog.set(rootProject.layout.projectDirectory.file("agent-server/maoer_agent/resources/error_catalog.json"))
    destination.set(generatedErrors)
}
tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompilationTask<*>>().configureEach {
    dependsOn(generateErrorCatalog)
}

kotlin {
    // 配置 Android 目标
    androidTarget {
        compilerOptions {
            jvmTarget.set(JvmTarget.JVM_11)
        }
    }

    // 配置 iOS 目标
    listOf(
        iosX64(),
        iosArm64(),
        iosSimulatorArm64()
    ).forEach { iosTarget ->
        iosTarget.binaries.framework {
            baseName = "ComposeApp"
            isStatic = true
        }
    }

    sourceSets {
        commonMain.get().kotlin.srcDir(generatedErrors)
        commonMain.dependencies {
            implementation("io.github.pdvrieze.xmlutil:core:0.86.3")
            implementation("com.squareup.okio:okio:3.9.1")
            // Compose 基础库 (由插件提供便捷访问)
            implementation(compose.runtime)
            implementation(compose.foundation)
            implementation(compose.material3)
            implementation(compose.ui)
            implementation(compose.components.resources)
            implementation(compose.components.uiToolingPreview)
            implementation(compose.materialIconsExtended)
            implementation(libs.markdown.m3)
            implementation(libs.androidx.datastore.preferences.core)

            // 协程
            implementation(libs.kotlinx.coroutines.core)

            // Ktor 网络核心
            implementation(libs.ktor.client.core)
            implementation(libs.ktor.client.content.negotiation)
            implementation(libs.ktor.serialization.kotlinx.json)

            // Koin 依赖注入
            implementation(libs.koin.core)
            implementation(libs.koin.compose)

            // Voyager 路由
            implementation(libs.voyager.navigator)
            implementation(libs.voyager.screenmodel)
            implementation(libs.voyager.koin)

            // Coil 图片加载
            implementation(libs.coil.compose)
            implementation(libs.coil.network.ktor)
        }

        androidMain.dependencies {
            implementation(libs.ssh.jsch)
            implementation(libs.androidx.activity.compose)
            implementation(libs.androidx.core.ktx)

            // Android 特有的 Ktor 引擎
            implementation(libs.ktor.client.okhttp)
            // Android 特有的协程
            implementation(libs.kotlinx.coroutines.android)

            // Media3
            implementation(libs.androidx.media3.exoplayer)
            implementation(libs.androidx.media3.session)
            implementation(libs.androidx.media3.common)
        }

        // iOS 网络引擎配置
        iosMain.dependencies {
            implementation(libs.ktor.client.darwin)
        }
    }
}

android {
    namespace = "com.maoer.lite"
    // 注意：TOML 中的 android-compileSdk 在这里会变成驼峰命名 android.compileSdk
    compileSdk = libs.versions.android.compileSdk.get().toInt()

    defaultConfig {
        applicationId = "com.maoer.lite"
        minSdk = libs.versions.android.minSdk.get().toInt()
        targetSdk = libs.versions.android.targetSdk.get().toInt()
        versionCode = 2
        versionName = "1.1.0-dev"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
}
