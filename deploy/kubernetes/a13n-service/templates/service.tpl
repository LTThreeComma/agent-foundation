# Control serves the API; workers accept no traffic.
apiVersion: v1
kind: Service
metadata:
  name: {{ include "a13n.name" . }}-control
  {{- with .Values.serviceAnnotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: ClusterIP
  selector:
    {{- include "a13n.selector" . | nindent 4 }}
    app.kubernetes.io/component: control
  ports:
    - name: http
      port: 8000
      targetPort: http
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "a13n.name" . }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
automountServiceAccountToken: false
