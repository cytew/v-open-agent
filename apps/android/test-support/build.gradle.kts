plugins {
    alias(libs.plugins.kotlin.jvm)
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    api(project(":protocol"))
    implementation(libs.kotlinx.coroutines.core)
    api(libs.kotlinx.coroutines.test)
}

