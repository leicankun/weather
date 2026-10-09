<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
const props = defineProps({ text: { type: String, default: '' } })
const html = computed(() =>
  DOMPurify.sanitize(marked.parse(props.text, { breaks: true, async: false })),
)
</script>
<template><div class="markdown" v-html="html" /></template>
