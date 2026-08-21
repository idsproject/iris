package aws

import ()

func UploadArticle(localFilePath string) error {
	awsConn, err := NewAwsS3Connection(nil)
	if err != nil {
		return err
	}

	return nil
}
