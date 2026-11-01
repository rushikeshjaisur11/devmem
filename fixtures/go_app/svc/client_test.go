package svc

import "testing"

func TestLoadUser(t *testing.T) {
	if err := LoadUser(&Client{}, "1"); err != nil {
		t.Fatal(err)
	}
}
