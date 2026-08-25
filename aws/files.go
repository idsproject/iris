package aws

import (
	"context"
	"os"
	"path/filepath"
	"strings"
)

func UploadArticle(localFilePath string) error {
	awsConn, err := NewAwsS3Connection(context.Background())
	if err != nil {
		return err
	}

	if !filepath.IsLocal(localFilePath) {
		return ErrBadPath
	}
	file, err := os.Open(localFilePath) // #nosec G304
	if err != nil {
		return err
	}

	fileInfo, err := file.Stat()
	if err != nil {
		return err
	}

	fileName := "pdf/" + fileInfo.Name()

	bucketName := os.Getenv("PDF_BUCKET_NAME")

	err = awsConn.uploadFile(file, bucketName, fileName)
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
