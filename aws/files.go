package aws

import (
	"context"
	"io"
	"os"
	"strings"
)

func UploadArticle(file io.Reader, fileName string, fileSize *int64) error {
	awsConn, err := NewAwsS3Connection(context.Background())
	if err != nil {
		return err
	}

	bucketName := os.Getenv("HTML_BUCKET_NAME")
	objectKey := "uploads/" + fileName

	err = awsConn.uploadFile(file, bucketName, objectKey, fileSize)
	if err != nil {
		return err
	}

	return nil
}

func DownloadArticle(fileName string) error {
	awsConn, err := NewAwsS3Connection(context.Background())
	if err != nil {
		return err
	}

	bucketName := os.Getenv("PDF_BUCKET_NAME")
	downloadDir := os.Getenv("DOWNLOAD_DIR") + "/" + fileName
	objectName := "result/COMPLIANT_" + fileName

	objects, err := awsConn.listObjects(bucketName)
	if err != nil {
		return err
	}

	for _, object := range objects {
		parts := strings.Split(object.Key, "/")
		if parts[0] == "result" && parts[1] == "COMPLIANT_"+fileName {
			data, err := awsConn.downloadFile(bucketName, objectName)
			if err != nil {
				return err
			}

			err = os.WriteFile(downloadDir, data, 0644) // #nosec G703
			if err != nil {
				return err
			}

			err = awsConn.deleteFile(bucketName, objectName, "", false)
			if err != nil {
				return err
			}
		}
	}

	return nil
}

// DownloadLayout copies an object the iris-layout Lambda wrote to the
// pdf2html bucket (HTML_BUCKET_NAME) to destPath. The object is left in S3:
// the Lambda checks for it to avoid rebuilding an article twice.
func DownloadLayout(objectKey string) ([]byte, error) {
	bucketName := os.Getenv("HTML_BUCKET_NAME")
	if bucketName == "" {
		return nil, ErrNoBucket
	}

	awsConn, err := NewAwsS3Connection(context.Background())
	if err != nil {
		return nil, err
	}

	return awsConn.downloadFile(bucketName, objectKey)
}
