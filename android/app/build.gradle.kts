import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// A complete environment configuration takes precedence. Never combine a
// partially supplied environment with a private properties file.
val signingEnvironmentNames = mapOf(
    "storeFile" to "MODISH_SIGNING_STORE_FILE",
    "storePassword" to "MODISH_SIGNING_STORE_PASSWORD",
    "keyAlias" to "MODISH_SIGNING_KEY_ALIAS",
    "keyPassword" to "MODISH_SIGNING_KEY_PASSWORD",
)
val signingEnvironment = signingEnvironmentNames.mapValues { (_, name) ->
    providers.environmentVariable(name).orNull
}
val releaseSigningValues = mutableMapOf<String, String>()
val signingRequired =
    "MODISH_RELEASE_SIGNING_REQUIRED: Provide a complete external release signing configuration."
val signingInvalid =
    "MODISH_RELEASE_SIGNING_INVALID: Release signing configuration is incomplete or unusable."
var releaseSigningError: String? = null

if (signingEnvironment.values.any { it != null }) {
    signingEnvironment.forEach { (name, value) ->
        if (value != null) releaseSigningValues[name] = value
    }
} else {
    val propertiesFile = rootProject.file("key.properties")
    if (!propertiesFile.isFile) {
        releaseSigningError = signingRequired
    } else {
        try {
            val properties = Properties()
            propertiesFile.inputStream().use { properties.load(it) }
            signingEnvironmentNames.keys.forEach { name ->
                properties.getProperty(name)?.let { releaseSigningValues[name] = it }
            }
        } catch (_: Exception) {
            // Do not attach the cause: it may contain private paths or values.
            releaseSigningError = signingInvalid
        }
    }
}

if (releaseSigningError == null && signingEnvironmentNames.keys.any { name ->
        val value = releaseSigningValues[name]
        value.isNullOrBlank() || value.trim().startsWith("REPLACE_ME", ignoreCase = true)
    }) {
    releaseSigningError = signingInvalid
}
val releaseSigningStore = if (releaseSigningError == null) {
    try {
        rootProject.file(releaseSigningValues.getValue("storeFile").trim())
            .takeIf { it.isFile && it.canRead() }
    } catch (_: Exception) {
        null
    }
} else {
    null
}
if (releaseSigningError == null && releaseSigningStore == null) {
    releaseSigningError = signingInvalid
}

val validateProductionSigning = tasks.register("validateProductionSigning") {
    group = "verification"
    description = "Validate external release signing without printing private configuration."
    doLast {
        releaseSigningError?.let { throw GradleException(it) }
    }
}
// Inspect the actual graph, not just CLI task spelling: aliases/aggregate tasks
// and dry runs must not silently produce or advertise an unsigned release.
gradle.taskGraph.whenReady {
    if (allTasks.any { it.project.path == project.path && it.name.contains("release", ignoreCase = true) }) {
        releaseSigningError?.let { throw GradleException(it) }
    }
}

android {
    namespace = "com.example.modish"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_17.toString()
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.example.modish"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (releaseSigningError == null) {
            create("production") {
                storeFile = releaseSigningStore
                storePassword = releaseSigningValues.getValue("storePassword")
                keyAlias = releaseSigningValues.getValue("keyAlias").trim()
                keyPassword = releaseSigningValues.getValue("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("production")
        }
    }
}

tasks.configureEach {
    if (name.contains("release", ignoreCase = true)) {
        dependsOn(validateProductionSigning)
    }
}

flutter {
    source = "../.."
}
