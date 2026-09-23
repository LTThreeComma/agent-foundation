{{- range $role := list "control" "worker" }}
{{- with $ }}
{{- $settings := index .Values.roles $role }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "a13n.name" . }}-{{ $role }}
spec:
  replicas: {{ $settings.replicaCount }}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      {{- include "a13n.selector" . | nindent 6 }}
      app.kubernetes.io/component: {{ $role }}
  template:
    metadata:
      labels:
        {{- include "a13n.selector" . | nindent 8 }}
        app.kubernetes.io/component: {{ $role }}
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.tpl") . | sha256sum }}
        {{- with .Values.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      {{- include "a13n.podSpec" . | nindent 6 }}
      # Covers the worker's drain deadline and the server's graceful shutdown.
      terminationGracePeriodSeconds: 60
      initContainers:
        # Replicas never migrate: wait until the migration Job has brought the schema to this image's head.
        - name: wait-for-schema
          {{- include "a13n.container" . | nindent 10 }}
          command: ["sh", "-ec"]
          args:
            - until a13n-service --config /app/service.toml migrate --check; do sleep 5; done
          resources:
            {{- toYaml (default .Values.resources $settings.resources) | nindent 12 }}
      containers:
        - name: service
          {{- include "a13n.container" . | nindent 10 }}
          args: ["a13n-service", "--config", "/app/service.toml", "run", "--role", {{ $role | quote }}]
          ports:
            - name: http
              containerPort: 8000
          startupProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 5
            timeoutSeconds: 5
            failureThreshold: 60
          readinessProbe:
            httpGet:
              path: /readyz
              port: http
            periodSeconds: 10
            timeoutSeconds: 5
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 15
            timeoutSeconds: 5
          resources:
            {{- toYaml (default .Values.resources $settings.resources) | nindent 12 }}
      volumes:
        {{- include "a13n.volumes" . | nindent 8 }}
{{- end }}
{{- end }}
