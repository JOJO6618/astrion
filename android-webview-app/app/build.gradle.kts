import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

fun resolveConfig(name: String): String? {
    return (project.findProperty(name) as String?)?.takeIf { it.isNotBlank() }
        ?: System.getenv(name)?.takeIf { it.isNotBlank() }
}

// 后端服务地址（构建期注入，仓库中只保留占位符）：
// 优先级：-PHOME_URL 命令行参数 > local.properties 的 HOME_URL > 环境变量 HOME_URL > 占位默认值
// 自部署构建示例：./gradlew assembleRelease -PHOME_URL=https://your-server.example.com
fun resolveHomeUrl(): String {
    (project.findProperty("HOME_URL") as String?)?.takeIf { it.isNotBlank() }?.let { return it }
    val localPropsFile = rootProject.file("local.properties")
    if (localPropsFile.exists()) {
        val props = Properties()
        localPropsFile.inputStream().use { props.load(it) }
        props.getProperty("HOME_URL")?.takeIf { it.isNotBlank() }?.let { return it }
    }
    System.getenv("HOME_URL")?.takeIf { it.isNotBlank() }?.let { return it }
    return "https://agent.example.com"
}

val homeUrl: String = resolveHomeUrl()

android {
    namespace = "com.cyjai.agent"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.cyjai.agent"
        minSdk = 24
        targetSdk = 35
        versionCode = 45
        versionName = "1.0.43"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("String", "HOME_URL", "\"$homeUrl\"")
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        create("release") {
            val storeFilePath = resolveConfig("ANDROID_KEYSTORE_PATH")
            val storePass = resolveConfig("ANDROID_KEYSTORE_PASSWORD")
            val keyAliasValue = resolveConfig("ANDROID_KEY_ALIAS")
            val keyPass = resolveConfig("ANDROID_KEY_PASSWORD")

            if (!storeFilePath.isNullOrBlank()) {
                storeFile = file(storeFilePath)
            }
            storePassword = storePass
            keyAlias = keyAliasValue
            keyPassword = keyPass
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.activity:activity-ktx:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // PDF 预览（不依赖浏览器）
    implementation("com.github.mhiew:android-pdf-viewer:3.2.0-beta.3")

    // sherpa-onnx 语音识别库（需手动下载 AAR 放到 app/libs/）
    // 下载地址：https://huggingface.co/csukuangfj/sherpa-onnx-libs/tree/main/android/aar
    // 下载最新 sherpa-onnx-*.aar 放到 app/libs/ 目录即可
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar"))))
}
