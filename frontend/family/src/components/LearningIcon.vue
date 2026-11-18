<script setup lang="ts">
import home from '../static/icons/home.svg';
import camera from '../static/icons/camera.svg';
import book from '../static/icons/book.svg';
import review from '../static/icons/review.svg';
import calendar from '../static/icons/calendar.svg';
import check from '../static/icons/check.svg';
import arrow from '../static/icons/arrow.svg';
import shield from '../static/icons/shield.svg';

const icons = { home, camera, book, review, calendar, check, arrow, shield };
defineProps<{ name: keyof typeof icons }>();
</script>

<template>
  <image class="learning-icon" :src="icons[name]" mode="aspectFit" aria-hidden="true" />
</template>

<style scoped>
.learning-icon { display: inline-block; width: 24px; height: 24px; flex-shrink: 0; vertical-align: middle; }
</style>
