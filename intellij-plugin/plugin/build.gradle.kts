// NOTE: this module was NOT compiled or run in the environment that
// authored it -- it requires the full IntelliJ Platform SDK, a
// multi-hundred-MB-to-multi-GB download disproportionate to what could be
// verified without a real IntelliJ instance to actually run the plugin
// in anyway. Everything in this file and this module's Kotlin sources is
// written by hand against IntelliJ Platform SDK conventions and is
// UNVERIFIED. See intellij-plugin/MANUAL_TEST.md for what running
// `./gradlew :plugin:runIde` for real is expected to prove.
//
// The kotlin("jvm") version below MUST match core's and the root's exactly
// -- Gradle resolves a single version per plugin ID across an entire
// multi-project build when using the `plugins {}` DSL, so this can't drift
// independently even though this module itself is uncompiled. This is a
// NEW unverified risk introduced by bumping core to Kotlin 2.4.10: whether
// IntelliJ Platform 2024.2's bundled Kotlin plugin (K2 compiler) actually
// supports building against Kotlin 2.4.10 is unknown without a real
// compile attempt. If `./gradlew :plugin:runIde` fails with a Kotlin/
// Platform version-compatibility error, the fix is most likely either
// targeting a newer IC version below (2024.2 -> something more current)
// or, if that's not viable, decoupling this module's language version via
// `kotlin { compilerOptions { languageVersion.set(...) } }` rather than
// downgrading core's Kotlin version project-wide.
plugins {
    kotlin("jvm") version "2.4.10"
    id("org.jetbrains.intellij.platform") version "2.1.0"
}

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    implementation(project(":core"))

    // core's runtime deps must be bundled into the plugin jar too, since
    // the IntelliJ Platform does not provide them.
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("com.macasaet.fernet:fernet-java8:1.5.0")

    intellijPlatform {
        create("IC", "2024.2")
        instrumentationTools()
    }
}

intellijPlatform {
    pluginConfiguration {
        name = "Decoy"
        ideaVersion {
            sinceBuild = "242"
            untilBuild = provider { null } // no known upper bound; revisit if a future platform release breaks compatibility
        }
    }
}

kotlin {
    jvmToolchain(17)
}
