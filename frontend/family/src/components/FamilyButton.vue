<script setup lang="ts">
defineOptions({ inheritAttrs: false });
defineProps<{ disabled?: boolean }>();
const emit = defineEmits<{ click: [event: unknown] }>();
const nativeButton = 'button';
</script>

<template>
  <!-- #ifdef H5 -->
  <component :is="nativeButton" class="family-button" type="button" v-bind="$attrs" :disabled="disabled" @click="emit('click', $event)"><slot /></component>
  <!-- #endif -->
  <!-- #ifndef H5 -->
  <button class="family-button" :class="$attrs.class" :disabled="disabled" @click="emit('click', $event)"><slot /></button>
  <!-- #endif -->
</template>
