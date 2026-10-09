<script setup>
import { suggestPlaces } from '../api/places.js'
defineProps({ modelValue: String, placeholder: String })
const emit = defineEmits(['update:modelValue', 'select'])
async function suggestions(query, callback) {
  if (!query.trim()) {
    callback([])
    return
  }
  try {
    callback(
      (await suggestPlaces(query, '广州')).map((p) => ({
        ...p,
        value: p.name || p.title || p.address,
      })),
    )
  } catch {
    callback([])
  }
}
</script>
<template>
  <el-autocomplete
    :model-value="modelValue"
    :placeholder="placeholder"
    :fetch-suggestions="suggestions"
    :trigger-on-focus="false"
    style="width: 100%"
    @update:model-value="emit('update:modelValue', $event)"
    @select="emit('select', $event)"
  />
</template>
