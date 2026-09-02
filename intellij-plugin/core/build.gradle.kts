plugins {
    kotlin("jvm") version "2.4.10"
    kotlin("plugin.serialization") version "2.4.10"
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")
    implementation("com.macasaet.fernet:fernet-java8:1.5.0")

    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.3")
}

tasks.test {
    useJUnitPlatform()
    // PanelController calls readAuditLog/writeAuditLog/etc. with no `env`
    // override (PanelHost has no such parameter -- that mirrors the real
    // plugin, which just relies on the process environment or the key
    // file), so PanelControllerTest needs this set on the actual test JVM
    // rather than passed as a Kotlin-level parameter like the other test
    // classes do. This is a fixed, non-secret local test fixture key, not
    // a real credential.
    environment("DECOY_ENCRYPTION_KEY", "nhtXQTnXQzKW_0m_5r-ffK8dcl03BLSzJOOdmF8bDk4=")
}

kotlin {
    jvmToolchain(17)
}
