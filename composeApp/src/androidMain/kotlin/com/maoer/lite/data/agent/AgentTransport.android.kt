package com.maoer.lite.data.agent

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.jcraft.jsch.HostKey
import com.jcraft.jsch.HostKeyRepository
import com.jcraft.jsch.JSch
import com.jcraft.jsch.Session
import com.jcraft.jsch.UserInfo
import io.ktor.http.Url
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

actual class AgentTransport actual constructor() {
    private val lock = Mutex()
    @Volatile private var session: Session? = null
    private var profile: AgentConnection? = null
    private var localPort = 0

    actual suspend fun endpoint(connection: AgentConnection): String = withContext(Dispatchers.IO) {
        val ssh = connection.ssh ?: return@withContext agentServiceUrl(connection.url)
        lock.withLock {
            if (profile == connection && session?.isConnected == true) return@withLock "http://127.0.0.1:$localPort"
            close()
            ssh.validate()
            val target = Url(agentServiceUrl(connection.url))
            require(target.protocol.name == "http")
            val pin = PinnedHostKey(ssh.fingerprint)
            val next = newSession(ssh, pin)
            session = next
            try {
                next.setPassword(ssh.password.toByteArray(Charsets.UTF_8))
                next.connect(10_000)
                currentCoroutineContext().ensureActive()
                if (session !== next) throw AgentTransportException("ssh_connection_failed")
                localPort = next.setPortForwardingL("127.0.0.1", 0, target.host, target.port)
                if (session !== next) throw AgentTransportException("ssh_connection_failed")
                profile = connection
                "http://127.0.0.1:$localPort"
            } catch (e: Exception) {
                next.disconnect()
                close()
                currentCoroutineContext().ensureActive()
                throw AgentTransportException(when {
                    pin.seen != null && pin.seen != ssh.fingerprint -> "ssh_host_key_changed"
                    e.message?.contains("Auth fail", ignoreCase = true) == true -> "ssh_auth_failed"
                    else -> "ssh_connection_failed"
                })
            }
        }
    }

    actual suspend fun probe(ssh: AgentSshConfig): String = withContext(Dispatchers.IO) {
        ssh.validate(requireCredentials = false)
        val pin = PinnedHostKey("")
        val probe = newSession(ssh, pin)
        try {
            // Strict checking rejects the untrusted key before password authentication.
            runCatching { probe.connect(10_000) }
            currentCoroutineContext().ensureActive()
            pin.seen ?: throw AgentTransportException("ssh_connection_failed")
        } finally { probe.disconnect() }
    }

    actual fun close() {
        val old = session
        session = null
        profile = null
        localPort = 0
        old?.disconnect()
    }

    private fun newSession(ssh: AgentSshConfig, pin: PinnedHostKey): Session = JSch().apply {
        hostKeyRepository = pin
    }.getSession(ssh.username, ssh.host, ssh.port).apply {
        setConfig("StrictHostKeyChecking", "yes")
        setConfig("PreferredAuthentications", "password")
        // Android's standard providers support ECDSA and RSA SHA-2 without global provider changes.
        setConfig("server_host_key", "ecdsa-sha2-nistp256,rsa-sha2-512,rsa-sha2-256")
        setConfig("kex", "ecdh-sha2-nistp256,diffie-hellman-group14-sha256")
        setServerAliveInterval(15_000)
        setServerAliveCountMax(2)
        setTimeout(35_000)
        setDaemonThread(true)
    }

    private class PinnedHostKey(private val expected: String) : HostKeyRepository {
        var seen: String? = null
        override fun check(host: String?, key: ByteArray): Int {
            val actual = "SHA256:" + Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(key), Base64.NO_WRAP or Base64.NO_PADDING)
            seen = actual
            return if (expected.isNotEmpty() && MessageDigest.isEqual(expected.toByteArray(), actual.toByteArray())) HostKeyRepository.OK else HostKeyRepository.CHANGED
        }
        override fun add(hostkey: HostKey?, ui: UserInfo?) = Unit
        override fun remove(host: String?, type: String?) = Unit
        override fun remove(host: String?, type: String?, key: ByteArray?) = Unit
        override fun getKnownHostsRepositoryID() = "Maoer pinned server key"
        override fun getHostKey(): Array<HostKey> = emptyArray()
        override fun getHostKey(host: String?, type: String?): Array<HostKey> = emptyArray()
    }
}

private object ConnectionCipher {
    private const val ALIAS = "maoer.agent.connection.v1"
    @Synchronized fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
}

actual fun protectAgentConnection(value: String): String {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, ConnectionCipher.key())
    cipher.updateAAD("maoer-agent-connection-v1".toByteArray())
    return "enc:v1:" + Base64.encodeToString(cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8)), Base64.NO_WRAP)
}

actual fun unprotectAgentConnection(value: String): String {
    val bytes = Base64.decode(value.removePrefix("enc:v1:"), Base64.NO_WRAP)
    require(bytes.size > 28)
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.DECRYPT_MODE, ConnectionCipher.key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
    cipher.updateAAD("maoer-agent-connection-v1".toByteArray())
    return cipher.doFinal(bytes.copyOfRange(12, bytes.size)).toString(Charsets.UTF_8)
}
