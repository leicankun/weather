<script setup>
defineProps({ title: String, description: String })
</script>
<template>
  <header class="page-title">
    <div>
      <h1>{{ title }}</h1>
      <p v-if="description">{{ description }}</p>
    </div>
    <div class="actions"><slot /></div>
  </header>
</template>
