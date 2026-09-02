// Root build file. Intentionally minimal -- each subproject configures
// its own plugins/dependencies. See core/build.gradle.kts (pure Kotlin,
// fully buildable/testable in this environment) and
// plugin/build.gradle.kts (depends on the IntelliJ Platform SDK; see
// MANUAL_TEST.md for why that module was not compiled in this environment).

plugins {
    kotlin("jvm") version "2.4.10" apply false
}
