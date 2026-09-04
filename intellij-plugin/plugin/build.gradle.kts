// UPDATE (Phase 13): this module WAS, for the first time, actually
// compiled and packaged against the real IntelliJ Platform SDK in the
// environment that did the Phase 13 JCEF migration --
// `./gradlew :plugin:compileKotlin` and `./gradlew :plugin:buildPlugin`
// both ran to BUILD SUCCESSFUL against a real downloaded `IC-2024.2`
// distribution (Kotlin 2.4.10 against its bundled K2 compiler posed no
// problem -- the "NEW unverified risk" flagged below did not materialize),
// producing a real installable `plugin/build/distributions/plugin.zip`.
// `buildPlugin`'s `buildSearchableOptions` step even launched a real
// (headless) IDE instance to introspect the plugin and logged
// "JCEF is manually disabled in headless env via
// 'ide.browser.jcef.headless.enabled=false'" -- confirming
// `JBCefApp.isSupported()` correctly reports false in that environment
// and `DecoyProjectService`'s Swing/JEditorPane fallback path was
// genuinely exercised, not just theorized. See
// `DecoyProjectService.kt`'s class-level doc comment for the full JCEF
// investigation and decision, and `MANUAL_TEST.md`'s "JCEF vs Swing"
// section for exactly what this does and does NOT prove (no real GUI
// `runIde` session with a display was run -- interactive/visual behavior
// is still unverified).
//
// The kotlin("jvm") version below MUST match core's and the root's
// exactly -- Gradle resolves a single version per plugin ID across an
// entire multi-project build when using the `plugins {}` DSL.
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
    // VERIFIED FINDING (Phase 13, via a real `:plugin:verifyPluginProjectConfiguration`
    // run against the real IC-2024.2 SDK in this environment): the Platform
    // actually wants sourceCompatibility=21 for target 242 --
    // "The Java configuration specifies sourceCompatibility='17' but
    // IntelliJ Platform '2024.2' requires sourceCompatibility='21'." Left
    // at 17 anyway because that's the only JDK available in this sandbox
    // (no JDK 21 installed, and settings.gradle.kts configures no
    // toolchain auto-download repository, so `jvmToolchain(21)` hard-fails
    // here with "Cannot find a Java installation... matching
    // languageVersion=21" -- confirmed by actually trying it). Whoever
    // next builds this with a real JDK 21 available should bump this to
    // 21 -- do not carry this compromise forward silently.
    jvmToolchain(17)
}

// Packaging approach: unlike vsce (which supports per-OS/arch `--target`
// VSIX builds -- see vscode-extension/scripts/bundle-binaries.js's doc
// comment for that decision), the IntelliJ Platform Gradle plugin has no
// equivalent first-class "build N platform-specific plugin ZIPs from one
// module" mechanism that could be verified without a real Marketplace
// publish here. A single plugin distribution bundling all three
// platforms' `decoy-proxy` binaries (~100MB total, same ~34MB/binary
// figure as the VS Code side) is therefore the simpler and more
// consistent choice for this module too -- one `buildPlugin` output, no
// per-target packaging matrix to keep in sync with
// .github/workflows/release-binaries.yml.
//
// Naming convention (matches vscode-extension/scripts/bundle-binaries.js
// exactly, so both extensions could in principle share one binary tree):
// bin/<platform>-<arch>/decoy-proxy(.exe), where <platform> is
// darwin/win32/linux and <arch> is x64/arm64 -- see Actions.kt's
// findBundledDecoyProxyBinary() for the runtime lookup this produces for.
//
// This task only COPIES already-built binaries; it never invokes
// PyInstaller. In this environment only a macOS binary could actually be
// built (../../scripts/build_binary.py); the other two platforms come
// from release-binaries.yml's CI matrix, expected to stage its output
// under <repoRoot>/dist-<platform>-<arch>/ (or the current machine's
// plain <repoRoot>/dist/ for a local single-platform build) before this
// task runs, same convention the VS Code script uses.
val repoRoot = rootProject.projectDir.parentFile
// This directory's CONTENTS (not the directory name itself) become the
// resources root once added via sourceSets.main.resources.srcDir below --
// so the Copy task's `into("bin/<platform>-<arch>")` below is what
// produces the final `bin/<platform>-<arch>/decoy-proxy` resource path,
// not this directory's own "generated-resources" name.
val bundledBinariesDir = layout.buildDirectory.dir("generated-resources")

data class BinaryTarget(val platform: String, val arch: String, val binaryName: String)

val binaryTargets = listOf(
    BinaryTarget("darwin", "x64", "decoy-proxy"),
    BinaryTarget("darwin", "arm64", "decoy-proxy"),
    BinaryTarget("win32", "x64", "decoy-proxy.exe"),
    BinaryTarget("linux", "x64", "decoy-proxy"),
)

val bundleBinaries by tasks.registering(Copy::class) {
    val destRoot = bundledBinariesDir.get().asFile
    doFirst { destRoot.mkdirs() }
    for (target in binaryTargets) {
        val destDir = File(destRoot, "bin/${target.platform}-${target.arch}")
        val candidates = listOf(
            File(repoRoot, "dist-${target.platform}-${target.arch}/${target.binaryName}"),
            File(repoRoot, "dist/${target.binaryName}"), // only valid for the machine's own current platform
        )
        val source = candidates.firstOrNull { it.exists() }
        if (source != null) {
            from(source) {
                into("bin/${target.platform}-${target.arch}")
            }
        } else {
            logger.lifecycle("[bundleBinaries] no built binary found for ${target.platform}-${target.arch}, skipping")
        }
    }
    into(destRoot)
}

sourceSets {
    main {
        resources.srcDir(bundledBinariesDir)
    }
}

tasks.named("processResources") {
    dependsOn(bundleBinaries)
}
