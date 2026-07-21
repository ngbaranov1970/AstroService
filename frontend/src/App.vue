<script setup>
import { ref, onMounted } from 'vue'
import bridge from '@vkontakte/vk-bridge'

const userInfo = ref(null)
const error = ref(null)
const launchParams = ref('')

onMounted(async () => {
  launchParams.value = window.location.search
  bridge.send('VKWebAppInit', {})

  try {
    userInfo.value = await bridge.send('VKWebAppGetUserInfo', {})
  } catch (e) {
    error.value = e
  }
})

async function testStoryBox() {
  try {
    await bridge.send('VKWebAppShowStoryBox', {
      background_type: 'none',
    })
  } catch (e) {
    error.value = e
  }
}
</script>

<template>
  <div style="font-family: sans-serif; padding: 16px">
    <h1>VK Bridge — тестовая страница</h1>

    <h3>Launch params (тут же лежит sign)</h3>
    <pre style="white-space: pre-wrap; word-break: break-all">{{ launchParams || '(пусто — открыто не из VK)' }}</pre>

    <h3>VKWebAppGetUserInfo</h3>
    <pre>{{ userInfo }}</pre>

    <h3>Ошибка</h3>
    <pre>{{ error }}</pre>

    <button @click="testStoryBox">Проверить ShowStoryBox</button>
  </div>
</template>
