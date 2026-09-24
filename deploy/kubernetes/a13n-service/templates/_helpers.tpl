{{- define "a13n.name" -}}
{{- printf "%s-a13n" .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "a13n.selector" -}}
app.kubernetes.io/name: a13n-service
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "a13n.validate" -}}
{{- if and (eq .Values.objects.backend "s3") (not .Values.objects.bucket) -}}
{{- fail "objects.bucket is required for the s3 backend" -}}
{{- end -}}
{{- end -}}

{{/* Pod fields shared by both Service roles and the migration Job. */}}
{{- define "a13n.podSpec" -}}
serviceAccountName: {{ include "a13n.name" . }}
# Service link variables such as A13N_A13N_CONTROL_PORT would be rejected as unknown Service settings.
enableServiceLinks: false
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  fsGroupChangePolicy: OnRootMismatch
  seccompProfile:
    type: RuntimeDefault
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* Container fields shared by every Service process: image, hardening, credentials and configuration. */}}
{{- define "a13n.container" -}}
image: {{ printf "%s:%s" .Values.image.repository .Values.image.tag | quote }}
imagePullPolicy: {{ .Values.image.pullPolicy }}
securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop: [ALL]
envFrom:
  - secretRef:
      name: {{ .Values.existingSecret }}
volumeMounts:
  - name: config
    mountPath: /app/service.toml
    subPath: service.toml
    readOnly: true
  {{- if eq .Values.objects.backend "local" }}
  - name: objects
    mountPath: /app/var/objects
  {{- end }}
{{- end -}}

{{- define "a13n.volumes" -}}
- name: config
  configMap:
    name: {{ include "a13n.name" . }}-config
{{- if eq .Values.objects.backend "local" }}
- name: objects
  persistentVolumeClaim:
    claimName: {{ default (printf "%s-objects" (include "a13n.name" .)) .Values.persistence.existingClaim }}
{{- end }}
{{- end -}}
