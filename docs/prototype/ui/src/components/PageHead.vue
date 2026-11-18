<script setup>defineProps(['eyebrow','title','description']);</script>
<template><header class="page-head"><div><div class="eyebrow">{{eyebrow}}</div><h1>{{title}}</h1><p>{{description}}</p></div><div class="head-actions"><slot name="actions"><slot /></slot></div></header></template>
