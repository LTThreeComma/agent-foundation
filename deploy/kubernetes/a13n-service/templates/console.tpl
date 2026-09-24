{{- if .Values.console.enabled }}
apiVersion: v1
kind: Service
metadata:
  name: {{ include "a13n.name" . }}-console
  {{- with .Values.console.service.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: {{ .Values.console.service.type }}
  selector:
    app.kubernetes.io/name: a13n-console
    app.kubernetes.io/instance: {{ .Release.Name }}
  ports:
    - name: http
      port: 8000
      targetPort: http
      {{- if eq .Values.console.service.type "NodePort" }}
      nodePort: {{ .Values.console.service.nodePort }}
      {{- end }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "a13n.name" . }}-console
spec:
  replicas: {{ .Values.console.replicaCount }}
  selector:
    matchLabels:
      app.kubernetes.io/name: a13n-console
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: a13n-console
        app.kubernetes.io/instance: {{ .Release.Name }}
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 101
        runAsGroup: 101
        fsGroup: 101
        seccompProfile:
          type: RuntimeDefault
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: console
          image: {{ printf "%s:%s" .Values.console.image.repository .Values.console.image.tag | quote }}
          imagePullPolicy: {{ .Values.console.image.pullPolicy }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: [ALL]
          env:
            - name: A13N_SERVICE_UPSTREAM
              value: {{ printf "http://%s-control:8000" (include "a13n.name" .) | quote }}
          ports:
            - name: http
              containerPort: 8080
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
          resources:
            {{- toYaml .Values.console.resources | nindent 12 }}
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
{{- end }}
