plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}

// AGP 9 compiles Kotlin itself (built-in Kotlin). This classpath entry pins the
// Kotlin version used for that compilation and for the Compose/serialization
// compiler plugins, so all three agree on 2.4.21 instead of inheriting the
// older Kotlin runtime bundled with AGP.
buildscript {
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.21")
    }
}
