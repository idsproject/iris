package app

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestNotifyLayoutComplete posts "layout complete" callbacks through the
// router, so the OpenAPI validation runs as well as the handler. None of the
// cases reach S3: they are rejected first, or HTML_BUCKET_NAME is unset.
func TestNotifyLayoutComplete(t *testing.T) {
	t.Setenv("DOWNLOAD_DIR", t.TempDir())
	t.Setenv("HTML_BUCKET_NAME", "")

	app := &Application{Logger: slog.New(slog.NewTextHandler(io.Discard, nil))}
	router, err := app.routes()
	if err != nil {
		t.Fatal(err)
	}

	const invalidKey = "Invalid layout key"
	tests := []struct {
		name       string
		body       string
		wantMsg    string
		wantStatus int
	}{
		{
			name:       "no payload",
			body:       `{"sender":"aws-iris-layout","status":"success","file":"a.pdf","message":"layout complete"}`,
			wantStatus: http.StatusBadRequest,
			wantMsg:    invalidKey,
		},
		{
			name:       "key not a string",
			body:       `{"sender":"s","status":"success","file":"a.pdf","message":"layout complete","payload":{"key":7}}`,
			wantStatus: http.StatusBadRequest,
			wantMsg:    invalidKey,
		},
		{
			name:       "key outside iris/",
			body:       `{"sender":"s","status":"success","file":"a.pdf","message":"layout complete","payload":{"key":"uploads/a_print.html"}}`,
			wantStatus: http.StatusBadRequest,
			wantMsg:    invalidKey,
		},
		{
			name:       "key in a subfolder",
			body:       `{"sender":"s","status":"success","file":"a.pdf","message":"layout complete","payload":{"key":"iris/x/a_print.html"}}`,
			wantStatus: http.StatusBadRequest,
			wantMsg:    invalidKey,
		},
		{
			name:       "key is not the print file",
			body:       `{"sender":"s","status":"success","file":"a.pdf","message":"layout complete","payload":{"key":"iris/a_layout.html"}}`,
			wantStatus: http.StatusBadRequest,
			wantMsg:    invalidKey,
		},
		{
			name:       "key with no name",
			body:       `{"sender":"s","status":"success","file":"a.pdf","message":"layout complete","payload":{"key":"iris/_print.html"}}`,
			wantStatus: http.StatusBadRequest,
			wantMsg:    invalidKey,
		},
		{
			name:       "valid key, bucket not configured",
			body:       `{"sender":"s","status":"success","file":"My Article.pdf","message":"layout complete","payload":{"key":"iris/My_Article_print.html","bucket":"b"}}`,
			wantStatus: http.StatusInternalServerError,
			wantMsg:    "Error downloading layout from AWS",
		},
		{
			name:       "message outside the enum",
			body:       `{"sender":"s","status":"error","file":"a.pdf","message":"layout failed"}`,
			wantStatus: http.StatusBadRequest,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, "/v1/notify", strings.NewReader(tt.body))
			req.Header.Set("Content-Type", "application/json")
			rec := httptest.NewRecorder()

			router.ServeHTTP(rec, req)

			if rec.Code != tt.wantStatus {
				t.Fatalf("status = %d, want %d; body %s", rec.Code, tt.wantStatus, rec.Body)
			}
			if tt.wantMsg == "" {
				return
			}
			var resp struct {
				Message string `json:"message"`
			}
			err := json.Unmarshal(rec.Body.Bytes(), &resp)
			if err != nil {
				t.Fatalf("decoding %s: %v", rec.Body, err)
			}
			if resp.Message != tt.wantMsg {
				t.Errorf("message = %q, want %q", resp.Message, tt.wantMsg)
			}
		})
	}
}
