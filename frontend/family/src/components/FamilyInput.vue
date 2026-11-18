<script setup lang="ts">
defineOptions({ inheritAttrs: false });
withDefaults(defineProps<{ modelValue: string; password?: boolean; multiline?: boolean; type?: string; disabled?: boolean; placeholder?: string; maxlength?: string | number }>(), { type: 'text', maxlength: 140 });
const emit = defineEmits<{ 'update:modelValue': [value: string] }>();
const nativeInput = 'input', nativeTextarea = 'textarea';
function input(event: Event) { emit('update:modelValue', (event.target as HTMLInputElement).value); }
function uniInput(event: Event | { detail: { value: string } }) { emit('update:modelValue', (event as { detail: { value: string } }).detail.value); }
</script>

<template>
  <!-- #ifdef H5 -->
  <component :is="multiline ? nativeTextarea : nativeInput" v-bind="$attrs" :type="password ? 'password' : type" :value="modelValue" :disabled="disabled" :placeholder="placeholder" :maxlength="Number(maxlength)" @input="input" />
  <!-- #endif -->
  <!-- #ifndef H5 -->
  <textarea v-if="multiline" :class="$attrs.class" :value="modelValue" :disabled="disabled" :placeholder="placeholder" :maxlength="Number(maxlength)" @input="uniInput" />
  <input v-else :class="$attrs.class" :type="type" :password="password" :value="modelValue" :disabled="disabled" :placeholder="placeholder" :maxlength="Number(maxlength)" @input="uniInput" />
  <!-- #endif -->
</template>
