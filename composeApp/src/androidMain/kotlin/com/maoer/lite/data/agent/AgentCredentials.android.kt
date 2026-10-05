package com.maoer.lite.data.agent

import android.util.Base64
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

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
